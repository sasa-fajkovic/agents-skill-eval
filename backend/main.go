package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"mime/multipart"
	"net"
	"net/http"
	urlpkg "net/url"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/getsentry/sentry-go"
	"github.com/redis/go-redis/v9"
)

const (
	maxUploadSize    = 5 << 20
	queueKey         = "eval:queue"
	redisResultTTL   = time.Hour
	redisDialTimeout = 5 * time.Second
	workerPopTimeout = 5 * time.Second
	dockerTimeout    = 2 * time.Minute
	maxMemoryBytes   = 256
	maxQueueDepth    = 50
	rateLimitMax     = 5
	rateLimitTTL     = time.Hour
	githubFetchLimit = 5 << 20
	maxProgressLines = 200
)

var (
	allowedMimeTypes = map[string]bool{
		"text/plain":         true,
		"text/markdown":      true,
		"text/html":          true,
		"text/xml":           true,
		"application/pdf":    true,
		"application/xml":    true,
		"image/png":          true,
		"image/jpeg":         true,
		"application/json":   true,
		"text/x-python":      true,
		"text/x-sh":          true,
		"text/x-shellscript": true,
	}
	blockedExecutableExtensions = map[string]bool{
		".apk":  true,
		".app":  true,
		".bat":  true,
		".bin":  true,
		".cmd":  true,
		".com":  true,
		".dll":  true,
		".dmg":  true,
		".exe":  true,
		".gadget": true,
		".iso":  true,
		".jar":  true,
		".msi":  true,
		".pkg":  true,
		".ps1":  true,
		".scr":  true,
		".so":   true,
		".vbs":  true,
		".wsf":  true,
	}
	blockedFilenamePatterns = []securityPattern{
		{pattern: regexp.MustCompile(`(?i)^\.env(?:\..+)?$`), reason: "environment files are not allowed"},
		{pattern: regexp.MustCompile(`(?i)^(id_rsa|id_dsa|id_ecdsa|id_ed25519)$`), reason: "private key files are not allowed"},
		{pattern: regexp.MustCompile(`(?i)^(credentials\.json|service-account\.json|\.npmrc|\.netrc|\.pypirc)$`), reason: "credential files are not allowed"},
		{pattern: regexp.MustCompile(`(?i).+\.(pem|key|p12|pfx|asc)$`), reason: "secret-bearing key material is not allowed"},
	}
	blockedTextPatterns = []securityPattern{
		{pattern: regexp.MustCompile(`(?is)-----BEGIN [A-Z ]*PRIVATE KEY-----`), reason: "embedded private key material detected"},
		{pattern: regexp.MustCompile(`(?i)\b(sk-ant-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b`), reason: "embedded API token or access key detected"},
	}
	pdfBlockedPatterns = []securityPattern{
		{pattern: regexp.MustCompile(`(?is)/(javascript|js|launch|embeddedfile|openaction|richmedia)`), reason: "active PDF content is not allowed"},
	}
	redactionPatterns = []redactionPattern{
		{pattern: regexp.MustCompile(`(?is)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----`), replacement: "[REDACTED_PRIVATE_KEY]"},
		{pattern: regexp.MustCompile(`(?i)\b(sk-ant-[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b`), replacement: "[REDACTED_TOKEN]"},
		{pattern: regexp.MustCompile(`(?i)\b(Bearer\s+)[A-Za-z0-9._~-]+`), replacement: `${1}[REDACTED]`},
		{pattern: regexp.MustCompile(`(?i)\b(ANTHROPIC_API_KEY|API[_-]?KEY|TOKEN|SECRET|PASSWORD)\b\s*[:=]\s*['"]?[^'"\s,]+`), replacement: `${1}=[REDACTED]`},
	}
	errResultPending = errors.New("result pending")
)

type securityPattern struct {
	pattern *regexp.Regexp
	reason  string
}

type redactionPattern struct {
	pattern     *regexp.Regexp
	replacement string
}

type evalContainerResult struct {
	Status           string         `json:"status"`
	SkillName        string         `json:"skill_name"`
	SkillContent     string         `json:"skill_content"`
	SupportingContext string        `json:"supporting_context"`
	Deterministic    map[string]any `json:"deterministic"`
	Message          string         `json:"message"`
}

type application struct {
	rdb         *redis.Client
	frontendDir string
}

type evalJob struct {
	JobID        string `json:"jobId"`
	InputDir     string `json:"inputDir"`
	EnableLLM    bool   `json:"enableLlm"`
	LLMRequested bool   `json:"llmRequested,omitempty"`
}

type llmAnalysis struct {
	Strengths     []string `json:"strengths"`
	Weaknesses    []string `json:"weaknesses"`
	Suggestions   []string `json:"suggestions"`
	SecurityFlags []string `json:"security_flags"`
	QualityTier   string   `json:"quality_tier"`
	Provider      string   `json:"provider,omitempty"`
	Mode          string   `json:"mode,omitempty"`
}

type gitHubTarget struct {
	kind     string
	url      string
	owner    string
	repo     string
	ref      string
	path     string
	fileName string
	htmlURL   string
}

type gitHubDirectoryEntry struct {
	Name        string `json:"name"`
	Path        string `json:"path"`
	Type        string `json:"type"`
	DownloadURL string `json:"download_url"`
}

func main() {
	if err := sentry.Init(sentry.ClientOptions{
		Dsn:              os.Getenv("SENTRY_DSN"),
		Environment:      getenvDefault("SENTRY_ENVIRONMENT", getenvDefault("APP_ENV", "development")),
		SendDefaultPII:   false,
		AttachStacktrace: true,
		BeforeSend: func(event *sentry.Event, hint *sentry.EventHint) *sentry.Event {
			if event == nil {
				return nil
			}
			event.User = sentry.User{}
			event.Request = nil
			event.ServerName = ""
			event.Extra = map[string]any{}
			for key := range event.Contexts {
				if key != "runtime" && key != "os" && key != "device" && key != "trace" {
					delete(event.Contexts, key)
				}
			}
			for i := range event.Exception {
				event.Exception[i].Value = redactSecrets(event.Exception[i].Value)
				event.Exception[i].Type = strings.TrimSpace(event.Exception[i].Type)
			}
			event.Message = redactSecrets(event.Message)
			return event
		},
	}); err != nil {
		log.Printf("sentry init failed: %v", err)
	}
	defer sentry.Flush(2 * time.Second)
	if shouldEmitSentryTestEvent() {
		eventID := captureInfraEvent("sentry_test_event", errors.New("manual sentry connectivity test"), map[string]string{"component": "startup"})
		log.Printf("sentry test event sent: %s", eventID)
	}

	ctx := context.Background()
	redisAddr := getenvDefault("REDIS_ADDR", "127.0.0.1:6379")
	rdb := redis.NewClient(&redis.Options{
		Addr:        redisAddr,
		DialTimeout: redisDialTimeout,
	})
	if err := rdb.Ping(ctx).Err(); err != nil {
		captureInfraEvent("redis_startup_ping_failed", err, map[string]string{"component": "redis", "stage": "startup"})
		log.Printf("redis ping failed at startup: %v", err)
	}

	app := &application{
		rdb:         rdb,
		frontendDir: detectFrontendDir(),
	}

	go app.workerLoop(ctx)

	addr := ":" + getenvDefault("PORT", "8080")
	server := &http.Server{
		Addr:    addr,
		Handler: app.routes(),
	}

	log.Printf("starting server on %s", addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		captureInfraEvent("http_server_failed", err, map[string]string{"component": "http"})
		log.Fatal(err)
	}
}

func (app *application) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", app.handleHealth)
	mux.HandleFunc("POST /upload", app.handleUpload)
	mux.HandleFunc("GET /result/", app.handleResult)
	mux.HandleFunc("GET /faq", app.handleFAQ)
	mux.HandleFunc("GET /robots.txt", app.handleRobots)
	mux.HandleFunc("GET /sitemap.xml", app.handleSitemap)
	mux.HandleFunc("GET /", app.handleIndex)
	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServer(http.Dir(app.frontendDir))))

	return app.recoverPanics(app.uploadAbuseProtection(mux))
}

func (app *application) handleHealth(w http.ResponseWriter, r *http.Request) {
	status := http.StatusOK
	payload := map[string]any{"status": "ok", "redis": "ok"}
	if err := app.rdb.Ping(r.Context()).Err(); err != nil {
		status = http.StatusServiceUnavailable
		payload["status"] = "degraded"
		payload["redis"] = "error"
		payload["message"] = err.Error()
		captureInfraEvent("redis_healthcheck_failed", err, map[string]string{"component": "redis", "stage": "healthcheck"})
	}
	writeJSON(w, status, payload)
}

func (app *application) handleUpload(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadSize)
	if err := r.ParseMultipartForm(maxUploadSize); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid multipart upload or payload exceeds 5MB"})
		return
	}

	files := r.MultipartForm.File["files"]
	githubURL := strings.TrimSpace(r.FormValue("githubUrl"))
	enableLLM := parseBool(r.FormValue("enableLlm"))
	if len(files) == 0 && githubURL == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "provide at least one file or a GitHub URL"})
		return
	}

	jobID, err := newUUIDv4()
	if err != nil {
		sentry.CaptureException(err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to create job id"})
		return
	}

	inputDir := filepath.Join(os.TempDir(), "eval-"+jobID)
	if err := os.MkdirAll(inputDir, 0o755); err != nil {
		sentry.CaptureException(err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to prepare upload directory"})
		return
	}

	cleanup := true
	defer func() {
		if cleanup {
			_ = os.RemoveAll(inputDir)
		}
	}()

	for _, header := range files {
		if err := saveValidatedFile(inputDir, header); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
	}
	if githubURL != "" {
		if err := fetchGitHubFile(r.Context(), githubURL, inputDir); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
			return
		}
	}
	if err := scanUploadedPackage(inputDir); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}

	jobBytes, err := json.Marshal(evalJob{JobID: jobID, InputDir: inputDir, EnableLLM: enableLLM, LLMRequested: enableLLM})
	if err != nil {
		sentry.CaptureException(err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to serialize job"})
		return
	}
	if err := app.rdb.LPush(r.Context(), queueKey, jobBytes).Err(); err != nil {
		sentry.CaptureException(err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to enqueue job"})
		return
	}
	_ = app.setProgress(r.Context(), jobID, []string{"Upload received.", "Job queued."})

	cleanup = false
	writeJSON(w, http.StatusAccepted, map[string]string{"jobId": jobID})
}

func (app *application) handleResult(w http.ResponseWriter, r *http.Request) {
	jobID := strings.TrimPrefix(r.URL.Path, "/result/")
	if jobID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "job id is required"})
		return
	}

	if errorMessage, err := app.rdb.Get(r.Context(), redisErrorKey(jobID)).Result(); err == nil {
		progress := app.getProgress(r.Context(), jobID)
		var structured map[string]any
		if json.Unmarshal([]byte(errorMessage), &structured) == nil {
			structured["progress"] = progress
			writeJSON(w, http.StatusInternalServerError, structured)
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]any{"status": "error", "message": errorMessage, "progress": progress})
		return
	} else if err != nil && err != redis.Nil {
		sentry.CaptureException(err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to load job status"})
		return
	}

	result, err := app.rdb.Get(r.Context(), redisResultKey(jobID)).Result()
	if err == redis.Nil {
		progress := app.getProgress(r.Context(), jobID)
		if len(progress) == 0 {
			progress = []string{"Upload received.", "Waiting for worker..."}
		}
		writeJSON(w, http.StatusAccepted, map[string]any{"status": "pending", "progress": progress})
		return
	}
	if err != nil {
		sentry.CaptureException(err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to load job result"})
		return
	}

	progress := app.getProgress(r.Context(), jobID)
	var structured map[string]any
	if json.Unmarshal([]byte(result), &structured) == nil {
		structured["progress"] = progress
		writeJSON(w, http.StatusOK, structured)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(result))
}

func (app *application) handleIndex(w http.ResponseWriter, r *http.Request) {
	http.ServeFile(w, r, filepath.Join(app.frontendDir, "index.html"))
}

func (app *application) handleFAQ(w http.ResponseWriter, r *http.Request) {
	http.ServeFile(w, r, filepath.Join(app.frontendDir, "faq.html"))
}

func (app *application) handleRobots(w http.ResponseWriter, r *http.Request) {
	http.ServeFile(w, r, filepath.Join(app.frontendDir, "robots.txt"))
}

func (app *application) handleSitemap(w http.ResponseWriter, r *http.Request) {
	http.ServeFile(w, r, filepath.Join(app.frontendDir, "sitemap.xml"))
}

func (app *application) recoverPanics(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if recovered := recover(); recovered != nil {
				err := fmt.Errorf("panic: %v", recovered)
				captureInfraEvent("http_panic", err, map[string]string{"component": "http"})
				writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal server error"})
			}
		}()
		next.ServeHTTP(w, r)
	})
}

func (app *application) uploadAbuseProtection(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/upload" {
			next.ServeHTTP(w, r)
			return
		}
		if abuseProtectionDisabled() {
			next.ServeHTTP(w, r)
			return
		}

		queueLen, err := app.rdb.LLen(r.Context(), queueKey).Result()
		if err != nil {
			captureInfraEvent("queue_check_failed", err, map[string]string{"component": "redis", "stage": "queue_depth"})
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "queue check failed"})
			return
		}
		if queueLen > maxQueueDepth {
			writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "Service busy"})
			return
		}

		ip := clientIP(r)
		key := "ratelimit:" + ip
		count, err := app.rdb.Incr(r.Context(), key).Result()
		if err != nil {
			captureInfraEvent("rate_limit_check_failed", err, map[string]string{"component": "redis", "stage": "rate_limit"})
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "rate limit check failed"})
			return
		}
		if count == 1 {
			if err := app.rdb.Expire(r.Context(), key, rateLimitTTL).Err(); err != nil {
				captureInfraEvent("rate_limit_expire_failed", err, map[string]string{"component": "redis", "stage": "rate_limit_expire"})
			}
		}
		if count > rateLimitMax {
			writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "rate limit exceeded"})
			return
		}

		next.ServeHTTP(w, r)
	})
}

func (app *application) workerLoop(parent context.Context) {
	for {
		result, err := app.rdb.BRPop(parent, workerPopTimeout, queueKey).Result()
		if err == redis.Nil {
			continue
		}
		if err != nil {
			captureInfraEvent("worker_pop_failed", err, map[string]string{"component": "worker", "stage": "queue_pop"})
			log.Printf("worker pop failed: %v", err)
			time.Sleep(time.Second)
			continue
		}
		if len(result) < 2 {
			continue
		}

		var job evalJob
		if err := json.Unmarshal([]byte(result[1]), &job); err != nil {
			captureInfraEvent("worker_job_decode_failed", err, map[string]string{"component": "worker", "stage": "job_decode"})
			log.Printf("worker job decode failed: %v", err)
			continue
		}

		app.processJob(parent, job)
	}
}

func (app *application) processJob(parent context.Context, job evalJob) {
	defer func() {
		_ = os.RemoveAll(job.InputDir)
	}()
	_ = app.appendProgress(parent, job.JobID, "Worker picked up job.")

	ctx, cancel := context.WithTimeout(parent, dockerTimeout)
	defer cancel()
	dockerImage := getenvDefault("EVAL_DOCKER_IMAGE", "ghcr.io/sasa-fajkovic/agents-skill-eval:latest")
	_ = app.appendProgress(parent, job.JobID, "Starting isolated evaluator container...")

	args := []string{
		"run",
		"--rm",
		"--stop-timeout", "5",
		"--network", "none",
		"--read-only",
		"--tmpfs", "/tmp:size=50m",
		"--memory", fmt.Sprintf("%dm", maxMemoryBytes),
		"--cpus", "0.5",
		"--pids-limit", "50",
		"--security-opt", "no-new-privileges",
		"--cap-drop", "ALL",
		"--user", "1000:1000",
		"-v", job.InputDir+":/input:ro",
		dockerImage,
	}

	cmd := exec.CommandContext(ctx, "docker", args...)
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		sentry.CaptureException(err)
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), redactSecrets(err.Error()), redisResultTTL).Err()
		return
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		sentry.CaptureException(err)
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), redactSecrets(err.Error()), redisResultTTL).Err()
		return
	}
	if err := cmd.Start(); err != nil {
		captureInfraEvent("docker_start_failed", err, map[string]string{"component": "worker", "stage": "docker_start"})
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), redactSecrets(err.Error()), redisResultTTL).Err()
		return
	}

	stdoutCh := make(chan string, 1)
	stderrCh := make(chan []string, 1)
	go func() {
		bytes, _ := io.ReadAll(stdoutPipe)
		stdoutCh <- redactSecrets(strings.TrimSpace(string(bytes)))
	}()
	go func() {
		stderrCh <- app.collectProgress(job.JobID, stderrPipe)
	}()

	err = cmd.Wait()
	stdoutOutput := <-stdoutCh
	stderrLines := <-stderrCh

	if err != nil {
		message := err.Error()
		trimmedOutput := stdoutOutput
		if trimmedOutput == "" && len(stderrLines) > 0 {
			trimmedOutput = strings.TrimSpace(strings.Join(stderrLines, "\n"))
		}
		preserveOutput := false
		if ctx.Err() == context.DeadlineExceeded {
			message = "evaluation timed out"
			_ = app.appendProgress(parent, job.JobID, "Evaluation timed out.")
		}
		if trimmedOutput != "" {
			var parsed map[string]any
			if json.Unmarshal([]byte(trimmedOutput), &parsed) == nil {
				message = trimmedOutput
				preserveOutput = true
			} else {
				message = trimmedOutput
			}
		}
		if exitErr := new(exec.ExitError); errors.As(err, &exitErr) && len(exitErr.Stderr) > 0 && !preserveOutput {
			message = strings.TrimSpace(string(exitErr.Stderr))
		}
		captureInfraEvent("docker_eval_failed", err, map[string]string{"component": "worker", "stage": "docker_wait"})
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), redactSecrets(message), redisResultTTL).Err()
		return
	}

	_ = app.appendProgress(parent, job.JobID, "Preparing deterministic result...")
	trimmed := stdoutOutput
	if trimmed == "" {
		trimmed = `{"status":"error","message":"empty evaluation output"}`
	}
	finalResult, err := app.finalizeEvaluation(parent, job, trimmed)
	if err != nil {
		captureInfraEvent("finalize_evaluation_failed", err, map[string]string{"component": "worker", "stage": "finalize"})
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), redactSecrets(err.Error()), redisResultTTL).Err()
		return
	}
	if err := app.rdb.Set(parent, redisResultKey(job.JobID), redactSecrets(finalResult), redisResultTTL).Err(); err != nil {
		captureInfraEvent("result_store_failed", err, map[string]string{"component": "redis", "stage": "result_store"})
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), redactSecrets(err.Error()), redisResultTTL).Err()
	}
}

func saveValidatedFile(rootDir string, header *multipart.FileHeader) error {
	relPath, err := sanitizeRelativeUploadPath(header.Filename)
	if err != nil {
		return err
	}
	filename := filepath.Base(relPath)

	file, err := header.Open()
	if err != nil {
		return fmt.Errorf("failed to open %s", filename)
	}
	defer file.Close()

	sniff := make([]byte, 512)
	readBytes, err := file.Read(sniff)
	if err != nil && err != io.EOF {
		return fmt.Errorf("failed to inspect %s", filename)
	}
	sniff = sniff[:readBytes]
	mimeType := http.DetectContentType(sniff)
	if contentType := header.Header.Get("Content-Type"); contentType != "" {
		if parsed, _, parseErr := mime.ParseMediaType(contentType); parseErr == nil && parsed != "application/octet-stream" {
			mimeType = parsed
		}
	}
	if err := validateFileType(filename, mimeType); err != nil {
		return err
	}

	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return fmt.Errorf("failed to rewind %s", filename)
	}

	destination := filepath.Join(rootDir, filepath.FromSlash(relPath))
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return fmt.Errorf("failed to prepare %s", relPath)
	}
	out, err := os.Create(destination)
	if err != nil {
		return fmt.Errorf("failed to save %s", filename)
	}
	defer out.Close()

	if _, err := io.Copy(out, file); err != nil {
		return fmt.Errorf("failed to write %s", filename)
	}
	return nil
}

func validateFilenameSafety(filename string) error {
	if strings.Contains(filename, "..") {
		return fmt.Errorf("%s is not allowed", filename)
	}
	for _, candidate := range blockedFilenamePatterns {
		if candidate.pattern.MatchString(filename) {
			return fmt.Errorf("%s is not allowed: %s", filename, candidate.reason)
		}
	}
	return nil
}

func sanitizeRelativeUploadPath(name string) (string, error) {
	trimmed := strings.TrimSpace(strings.ReplaceAll(name, "\\", "/"))
	if trimmed == "" {
		return "", errors.New("invalid file name")
	}
	for _, part := range strings.Split(trimmed, "/") {
		if part == ".." {
			return "", fmt.Errorf("%s is not allowed", name)
		}
	}
	cleaned := strings.TrimPrefix(path.Clean("/"+trimmed), "/")
	if cleaned == "" || cleaned == "." {
		return "", errors.New("invalid file name")
	}
	parts := strings.Split(cleaned, "/")
	for _, part := range parts {
		if part == "" || part == "." || part == ".." {
			return "", fmt.Errorf("%s is not allowed", name)
		}
		if err := validateFilenameSafety(part); err != nil {
			return "", err
		}
	}
	return cleaned, nil
}

func scanUploadedPackage(rootDir string) error {
	var foundFiles bool
	err := filepath.WalkDir(rootDir, func(current string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return errors.New("failed to inspect uploaded package")
		}
		if current == rootDir {
			return nil
		}
		rel, err := filepath.Rel(rootDir, current)
		if err != nil {
			return errors.New("failed to inspect uploaded package")
		}
		if _, err := sanitizeRelativeUploadPath(filepath.ToSlash(rel)); err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		foundFiles = true
		return scanUploadedFile(current)
	})
	if err != nil {
		return err
	}
	if !foundFiles {
		return errors.New("uploaded package does not contain any files")
	}
	return nil
}

func scanUploadedFile(path string) error {
	content, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("failed to inspect %s", filepath.Base(path))
	}
	name := filepath.Base(path)
	ext := strings.ToLower(filepath.Ext(name))
	if ext == ".pdf" {
		for _, candidate := range pdfBlockedPatterns {
			if candidate.pattern.Match(content) {
				return fmt.Errorf("%s rejected: %s", name, candidate.reason)
			}
		}
		return nil
	}
	if !isScannableTextFile(name) {
		return nil
	}
	text := string(content)
	for _, candidate := range blockedTextPatterns {
		if candidate.pattern.MatchString(text) {
			return fmt.Errorf("%s rejected: %s", name, candidate.reason)
		}
	}
	return nil
}

func isScannableTextFile(name string) bool {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".md", ".markdown", ".txt", ".json", ".py", ".sh", ".js", ".yaml", ".yml":
		return true
	default:
		return false
	}
}

func (app *application) finalizeEvaluation(ctx context.Context, job evalJob, raw string) (string, error) {
	var container evalContainerResult
	if err := json.Unmarshal([]byte(raw), &container); err != nil {
		return "", errors.New("failed to parse evaluator output")
	}
	if container.Status == "error" {
		return "", errors.New(redactSecrets(container.Message))
	}
	if container.SkillContent == "" {
		return "", errors.New("evaluator did not return skill content")
	}

	_ = app.appendProgress(ctx, job.JobID, "Preparing final evaluation result...")

	var analysis *llmAnalysis
	if job.EnableLLM {
		if !llmEvaluationConfigured() {
			_ = app.appendProgress(ctx, job.JobID, "Optional LLM review requested, but the server is not configured for it. Returning deterministic result only.")
		} else {
			_ = app.appendProgress(ctx, job.JobID, "Optional LLM review requested. Running host-side review...")
			generated := generateLLMAnalysis(container.Deterministic)
			analysis = &generated
			_ = app.appendProgress(ctx, job.JobID, "Optional LLM review completed.")
		}
	}

	result := map[string]any{
		"status":        "ok",
		"skill_name":    container.SkillName,
		"overall_score": computeOverallScore(container.Deterministic, analysis),
		"summary":       summarizeIssues(container.Deterministic, analysis),
		"deterministic": container.Deterministic,
		"llm_enabled":   job.EnableLLM,
		"llm_requested": job.LLMRequested,
	}
	if analysis != nil {
		result["llm_analysis"] = analysis
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		return "", errors.New("failed to serialize final evaluation result")
	}
	_ = app.appendProgress(ctx, job.JobID, "Evaluation completed.")
	return string(encoded), nil
}

func deterministicIssues(value any) []string {
	raw, ok := value.([]any)
	if !ok {
		return nil
	}
	result := make([]string, 0, len(raw))
	for _, item := range raw {
		text := strings.TrimSpace(fmt.Sprint(item))
		if text != "" {
			result = append(result, text)
		}
	}
	return result
}

func computeOverallScore(deterministic map[string]any, analysis *llmAnalysis) int {
	issues := deterministicIssues(deterministic["issues"])
	score := 100 - min(len(issues)*10, 60)
	if analysis != nil {
		score = max(0, min(100, qualityTierBaseScore(analysis.QualityTier)-min(len(issues)*5, 30)))
	}
	if score < 0 {
		return 0
	}
	return score
}

func summarizeIssues(deterministic map[string]any, analysis *llmAnalysis) string {
	issues := deterministicIssues(deterministic["issues"])
	if len(issues) > 0 {
		return fmt.Sprintf("Skill evaluated with %d deterministic issue(s). Primary issue: %s.", len(issues), issues[0])
	}
	if analysis != nil && len(analysis.Strengths) > 0 {
		return fmt.Sprintf("Skill evaluated successfully. Key strength: %s", analysis.Strengths[0])
	}
	return "Skill evaluated successfully with no deterministic issues detected."
}

func llmEvaluationConfigured() bool {
	return strings.TrimSpace(os.Getenv("ANTHROPIC_API_KEY")) != ""
}

func generateLLMAnalysis(deterministic map[string]any) llmAnalysis {
	issues := deterministicIssues(deterministic["issues"])
	analysis := llmAnalysis{
		Strengths:     []string{"Deterministic checks completed inside an isolated container."},
		Weaknesses:    []string{},
		Suggestions:   []string{},
		SecurityFlags: []string{},
		QualityTier:   "good",
		Provider:      "host-configured",
		Mode:          "opt_in",
	}
	if len(issues) == 0 {
		analysis.Strengths = append(analysis.Strengths, "The uploaded skill passed all current deterministic checks.")
		analysis.Suggestions = append(analysis.Suggestions, "Keep examples and supporting context aligned as the skill evolves.")
		analysis.QualityTier = "excellent"
		return analysis
	}

	analysis.Weaknesses = append(analysis.Weaknesses, issues...)
	analysis.Suggestions = append(analysis.Suggestions, uniqueStrings(limitSuggestions(issues))...)
	analysis.QualityTier = "needs_work"
	if len(issues) >= 4 {
		analysis.QualityTier = "poor"
	}
	if slices.Contains(issues, "No error handling guidance") {
		analysis.SecurityFlags = append(analysis.SecurityFlags, "Missing failure guidance can make the skill less safe to operate.")
	}
	if slices.Contains(issues, "No examples provided") {
		analysis.Suggestions = append(analysis.Suggestions, "Add a concrete example that shows the expected invocation and output shape.")
	}
	return analysis
}

func limitSuggestions(issues []string) []string {
	results := make([]string, 0, len(issues))
	for _, issue := range issues {
		switch issue {
		case "Missing description section":
			results = append(results, "Add a short description section explaining the skill's purpose and boundaries.")
		case "Missing trigger/activation criteria":
			results = append(results, "Document when the skill should be used and what inputs should trigger it.")
		case "No examples provided":
			results = append(results, "Include at least one example so other agents can apply the skill consistently.")
		case "No error handling guidance":
			results = append(results, "Explain expected failure modes and the fallback behavior the agent should use.")
		case "Skill definition is very short (< 200 chars)":
			results = append(results, "Expand the skill so it includes enough structure to be portable across agents.")
		case "Skill definition is very long (> 50k chars) - consider splitting":
			results = append(results, "Split oversized instructions into focused sections or supporting files.")
		default:
			results = append(results, "Address the reported deterministic issues before relying on this skill in production.")
		}
	}
	return results
}

func uniqueStrings(values []string) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		result = append(result, value)
	}
	return result
}

func qualityTierBaseScore(tier string) int {
	switch strings.TrimSpace(tier) {
	case "excellent":
		return 95
	case "good":
		return 85
	case "poor":
		return 45
	default:
		return 70
	}
}

func parseIntDefault(value string, fallback int) int {
	if value == "" {
		return fallback
	}
	var parsed int
	if _, err := fmt.Sscanf(value, "%d", &parsed); err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}

func parseBool(value string) bool {
	value = strings.TrimSpace(strings.ToLower(value))
	return value == "1" || value == "true" || value == "yes" || value == "on"
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func fetchGitHubFile(ctx context.Context, rawURL, rootDir string) error {
	target, err := resolveGitHubTarget(rawURL)
	if err != nil {
		return err
	}
	if target.kind == "directory" {
		return fetchGitHubDirectory(ctx, target, rootDir)
	}

	client := &http.Client{
		Timeout: 15 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if !isAllowedGitHubHost(req.URL.Host) {
				return errors.New("redirected outside GitHub")
			}
			return nil
		},
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target.url, nil)
	if err != nil {
		return errors.New("failed to create GitHub request")
	}
	req.Header.Set("User-Agent", "agents-skill-eval")

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to fetch GitHub URL: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GitHub URL returned %s", resp.Status)
	}

	limited := io.LimitReader(resp.Body, githubFetchLimit+1)
	content, err := io.ReadAll(limited)
	if err != nil {
		return errors.New("failed to read GitHub file")
	}
	if len(content) > githubFetchLimit {
		return errors.New("GitHub file exceeds 5MB limit")
	}

	mimeType := http.DetectContentType(content)
	if headerType := resp.Header.Get("Content-Type"); headerType != "" {
		if parsed, _, parseErr := mime.ParseMediaType(headerType); parseErr == nil && parsed != "application/octet-stream" {
			mimeType = parsed
		}
	}
	if err := validateFileType(target.fileName, mimeType); err != nil {
		return err
	}

	destination := filepath.Join(rootDir, filepath.FromSlash(target.path))
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return fmt.Errorf("failed to prepare %s", target.fileName)
	}
	if err := os.WriteFile(destination, content, 0o644); err != nil {
		return fmt.Errorf("failed to save %s", target.fileName)
	}
	return nil
}

func resolveGitHubTarget(rawURL string) (gitHubTarget, error) {
	parsed, err := urlpkg.Parse(strings.TrimSpace(rawURL))
	if err != nil {
		return gitHubTarget{}, errors.New("invalid GitHub URL")
	}
	if parsed.Scheme != "https" && parsed.Scheme != "http" {
		return gitHubTarget{}, errors.New("GitHub URL must use http or https")
	}
	if !isAllowedGitHubHost(parsed.Host) {
		return gitHubTarget{}, errors.New("only github.com and raw.githubusercontent.com URLs are allowed")
	}

	parts := strings.Split(strings.Trim(parsed.Path, "/"), "/")
	if parsed.Host == "raw.githubusercontent.com" {
		if len(parts) < 4 {
			return gitHubTarget{}, errors.New("GitHub raw URL must point to a file")
		}
		filename := filepath.Base(parsed.Path)
		if filename == "." || filename == "/" || filename == "" {
			return gitHubTarget{}, errors.New("GitHub raw URL must point to a file")
		}
		parsed.RawQuery = ""
		parsed.Fragment = ""
		parsed.Scheme = "https"
		return gitHubTarget{kind: "file", url: parsed.String(), fileName: filename}, nil
	}
	if len(parts) < 4 {
		return gitHubTarget{}, errors.New("GitHub URL must point to a file or directory inside a repository")
	}
	if parts[2] != "blob" && parts[2] != "tree" {
		return gitHubTarget{}, errors.New("GitHub URL must point to github.com/.../blob/... or github.com/.../tree/...")
	}

	if parts[2] == "tree" {
		if len(parts) < 5 {
			return gitHubTarget{}, errors.New("GitHub tree URL must include a branch and path")
		}
		path := strings.Join(parts[4:], "/")
		if path == "" {
			return gitHubTarget{}, errors.New("GitHub tree URL must point to a directory")
		}
		parsed.RawQuery = ""
		parsed.Fragment = ""
		parsed.Scheme = "https"
		return gitHubTarget{kind: "directory", owner: parts[0], repo: parts[1], ref: parts[3], path: path, htmlURL: parsed.String()}, nil
	}

	ref, path, err := splitGitHubBlobPath(parts)
	if err != nil {
		return gitHubTarget{}, err
	}

	filename := filepath.Base(path)
	if filename == "." || filename == "/" || filename == "" {
		return gitHubTarget{}, errors.New("GitHub URL must point to a file")
	}
	raw := &urlpkg.URL{
		Scheme: "https",
		Host:   "raw.githubusercontent.com",
		Path:   "/" + strings.Join([]string{parts[0], parts[1], ref, path}, "/"),
	}
	return gitHubTarget{kind: "file", url: raw.String(), fileName: filename, owner: parts[0], repo: parts[1], ref: ref, path: path}, nil
}

func splitGitHubBlobPath(parts []string) (string, string, error) {
	if len(parts) < 5 {
		return "", "", errors.New("GitHub blob URL must include a branch and path")
	}
	return parts[3], strings.Join(parts[4:], "/"), nil
}

func fetchGitHubDirectory(ctx context.Context, target gitHubTarget, rootDir string) error {
	entries, err := fetchGitHubDirectoryEntries(ctx, target)
	if err != nil {
		return err
	}
	selected := []gitHubDirectoryEntry{}
	for _, entry := range entries {
		if entry.Type == "file" && (strings.EqualFold(filepath.Base(entry.Path), "SKILL.md") || strings.EqualFold(filepath.Base(entry.Path), "skill.md")) {
			selected = append(selected, entry)
		}
	}
	for _, entry := range entries {
		if entry.Type != "file" || !isAllowedSupportingSkillFile(entry.Path) {
			continue
		}
		base := filepath.Base(entry.Path)
		if strings.EqualFold(base, "SKILL.md") || strings.EqualFold(base, "skill.md") {
			continue
		}
		selected = append(selected, entry)
	}
	if len(selected) == 0 {
		return errors.New("GitHub directory must contain SKILL.md or skill.md")
	}
	for _, entry := range selected {
		fileURL := fmt.Sprintf("https://github.com/%s/%s/blob/%s/%s", target.owner, target.repo, target.ref, entry.Path)
		if err := fetchGitHubFile(ctx, fileURL, rootDir); err != nil {
			return err
		}
	}
	return nil
}

func fetchGitHubDirectoryEntries(ctx context.Context, target gitHubTarget) ([]gitHubDirectoryEntry, error) {
	apiURL := fmt.Sprintf("https://api.github.com/repos/%s/%s/git/trees/%s?recursive=1", target.owner, target.repo, urlpkg.PathEscape(target.ref))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
	if err != nil {
		return nil, errors.New("failed to create GitHub directory request")
	}
	req.Header.Set("User-Agent", "agents-skill-eval")
	req.Header.Set("Accept", "application/vnd.github+json")
	if token := strings.TrimSpace(os.Getenv("GITHUB_TOKEN")); token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return fetchGitHubDirectoryEntriesHTML(ctx, target)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusTooManyRequests {
		return fetchGitHubDirectoryEntriesHTML(ctx, target)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GitHub directory URL returned %s", resp.Status)
	}
	var tree struct {
		Tree []struct {
			Path string `json:"path"`
			Type string `json:"type"`
		} `json:"tree"`
		Truncated bool `json:"truncated"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tree); err != nil {
		return nil, errors.New("failed to decode GitHub directory listing")
	}
	prefix := strings.Trim(strings.TrimSpace(target.path), "/")
	if prefix == "" {
		return nil, errors.New("GitHub directory path is empty")
	}
	prefixWithSlash := prefix + "/"
	entries := []gitHubDirectoryEntry{}
	for _, entry := range tree.Tree {
		if !strings.HasPrefix(entry.Path, prefixWithSlash) {
			continue
		}
		relative := strings.TrimPrefix(entry.Path, prefixWithSlash)
		if relative == "" {
			continue
		}
		entryType := entry.Type
		if entryType == "blob" {
			entryType = "file"
		}
		if entryType == "tree" {
			entryType = "dir"
		}
		entries = append(entries, gitHubDirectoryEntry{
			Name: filepath.Base(relative),
			Path: entry.Path,
			Type: entryType,
		})
	}
	if len(entries) == 0 {
		return nil, errors.New("failed to discover files from GitHub directory")
	}
	return entries, nil
}

func fetchGitHubDirectoryEntriesHTML(ctx context.Context, target gitHubTarget) ([]gitHubDirectoryEntry, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target.htmlURL, nil)
	if err != nil {
		return nil, errors.New("failed to create GitHub directory request")
	}
	req.Header.Set("User-Agent", "agents-skill-eval")
	req.Header.Set("Accept", "text/html")
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to fetch GitHub directory: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GitHub directory URL returned %s", resp.Status)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, errors.New("failed to read GitHub directory page")
	}
	html := string(body)
	relativePath := strings.TrimPrefix(target.path, "/")
	entryPattern := regexp.MustCompile(`href="/[^"]+/(blob|tree)/` + regexp.QuoteMeta(target.ref) + `/` + regexp.QuoteMeta(relativePath) + `/([^"]+)"`)
	matches := entryPattern.FindAllStringSubmatch(html, -1)
	entries := []gitHubDirectoryEntry{}
	seen := map[string]bool{}
	for _, match := range matches {
		name := strings.TrimSpace(match[2])
		if name == "" || strings.Contains(name, "/") || seen[name] {
			continue
		}
		seen[name] = true
		entryType := "file"
		if match[1] == "tree" {
			entryType = "dir"
		}
		fullPath := strings.TrimPrefix(filepath.ToSlash(filepath.Join(relativePath, name)), "/")
		entry := gitHubDirectoryEntry{Name: name, Path: fullPath, Type: entryType}
		entries = append(entries, entry)
		if entryType == "dir" {
			nested, err := fetchGitHubDirectoryEntriesHTML(ctx, gitHubTarget{
				owner:   target.owner,
				repo:    target.repo,
				ref:     target.ref,
				path:    fullPath,
				htmlURL: fmt.Sprintf("https://github.com/%s/%s/tree/%s/%s", target.owner, target.repo, target.ref, fullPath),
			})
			if err != nil {
				return nil, err
			}
			entries = append(entries, nested...)
		}
	}
	if len(entries) == 0 {
		return nil, errors.New("failed to discover files from GitHub directory page")
	}
	return entries, nil
}

func isAllowedSupportingSkillFile(name string) bool {
	ext := strings.ToLower(filepath.Ext(name))
	switch ext {
	case ".md", ".markdown", ".txt", ".json", ".py", ".sh", ".js", ".yaml", ".yml", ".pdf", ".png", ".jpg", ".jpeg", ".html", ".xml", ".xsd":
		return true
	default:
		return false
	}
}

func isAllowedGitHubHost(host string) bool {
	host = strings.ToLower(host)
	return host == "github.com" || host == "raw.githubusercontent.com"
}

func validateFileType(filename, mimeType string) error {
	normalized := mimeType
	if parsed, _, err := mime.ParseMediaType(mimeType); err == nil {
		normalized = parsed
	}
	ext := strings.ToLower(filepath.Ext(filename))
	if blockedExecutableExtensions[ext] {
		return fmt.Errorf("%s is not an allowed upload type", filename)
	}
	if strings.HasPrefix(normalized, "application/x-") && normalized != "application/xml" {
		return fmt.Errorf("%s has unsupported executable MIME type %s", filename, mimeType)
	}
	if !allowedMimeTypes[normalized] {
		return fmt.Errorf("%s has unsupported MIME type %s", filename, mimeType)
	}

	if ext == ".sh" && normalized != "text/x-shellscript" && normalized != "text/plain" && normalized != "text/x-sh" {
		return fmt.Errorf("%s is not an allowed shell script upload", filename)
	}
	return nil
}

func newUUIDv4() (string, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	raw[6] = (raw[6] & 0x0f) | 0x40
	raw[8] = (raw[8] & 0x3f) | 0x80
	encoded := hex.EncodeToString(raw)
	return fmt.Sprintf("%s-%s-%s-%s-%s", encoded[0:8], encoded[8:12], encoded[12:16], encoded[16:20], encoded[20:32]), nil
}

func redisResultKey(jobID string) string {
	return "result:" + jobID
}

func redisErrorKey(jobID string) string {
	return "error:" + jobID
}

func redisProgressKey(jobID string) string {
	return "progress:" + jobID
}

func clientIP(r *http.Request) string {
	if forwarded := r.Header.Get("X-Forwarded-For"); forwarded != "" {
		parts := strings.Split(forwarded, ",")
		return strings.TrimSpace(parts[0])
	}
	if realIP := r.Header.Get("X-Real-IP"); realIP != "" {
		return realIP
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err == nil {
		return host
	}
	return r.RemoteAddr
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		http.Error(w, `{"error":"failed to encode response"}`, http.StatusInternalServerError)
	}
}

func getenvDefault(key, fallback string) string {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	return value
}

func redactSecrets(text string) string {
	redacted := text
	for _, candidate := range redactionPatterns {
		redacted = candidate.pattern.ReplaceAllString(redacted, candidate.replacement)
	}
	return redacted
}

func detectFrontendDir() string {
	if _, err := os.Stat(filepath.Join("frontend", "index.html")); err == nil {
		return "frontend"
	}
	return filepath.Join("..", "frontend")
}

func abuseProtectionDisabled() bool {
	value := strings.ToLower(strings.TrimSpace(os.Getenv("DISABLE_ABUSE_PROTECTION")))
	return value == "1" || value == "true" || value == "yes"
}

func (app *application) setProgress(ctx context.Context, jobID string, lines []string) error {
	if len(lines) == 0 {
		return nil
	}
	values := make([]any, 0, len(lines))
	for _, line := range lines {
		values = append(values, line)
	}
	key := redisProgressKey(jobID)
	if err := app.rdb.Del(ctx, key).Err(); err != nil {
		return err
	}
	if err := app.rdb.RPush(ctx, key, values...).Err(); err != nil {
		return err
	}
	return app.rdb.Expire(ctx, key, redisResultTTL).Err()
}

func (app *application) appendProgress(ctx context.Context, jobID, line string) error {
	line = strings.TrimSpace(redactSecrets(line))
	if line == "" {
		return nil
	}
	key := redisProgressKey(jobID)
	if err := app.rdb.RPush(ctx, key, line).Err(); err != nil {
		return err
	}
	if err := app.rdb.LTrim(ctx, key, -maxProgressLines, -1).Err(); err != nil {
		return err
	}
	return app.rdb.Expire(ctx, key, redisResultTTL).Err()
}

func (app *application) getProgress(ctx context.Context, jobID string) []string {
	lines, err := app.rdb.LRange(ctx, redisProgressKey(jobID), 0, -1).Result()
	if err != nil && err != redis.Nil {
		captureInfraEvent("progress_load_failed", err, map[string]string{"component": "redis", "stage": "progress_load"})
		return nil
	}
	return lines
}

func captureInfraEvent(kind string, err error, tags map[string]string) sentry.EventID {
	if err == nil {
		return ""
	}
	var eventID sentry.EventID
	sentry.WithScope(func(scope *sentry.Scope) {
		scope.Clear()
		scope.SetTag("event_kind", kind)
		scope.SetLevel(sentry.LevelError)
		for key, value := range tags {
			scope.SetTag(key, value)
		}
		scope.SetContext("infra", map[string]any{"kind": kind})
		captured := sentry.CaptureException(errors.New(redactSecrets(err.Error())))
		if captured != nil {
			eventID = *captured
		}
	})
	return eventID
}

func shouldEmitSentryTestEvent() bool {
	value := strings.TrimSpace(strings.ToLower(os.Getenv("SENTRY_TEST_EVENT")))
	return value == "1" || value == "true" || value == "yes"
}

func (app *application) collectProgress(jobID string, reader io.Reader) []string {
	scanner := bufio.NewScanner(reader)
	buffer := make([]byte, 0, 64*1024)
	scanner.Buffer(buffer, 1024*1024)
	lines := []string{}
	for scanner.Scan() {
		line := strings.TrimSpace(redactSecrets(scanner.Text()))
		if line == "" {
			continue
		}
		lines = append(lines, line)
		if len(lines) > maxProgressLines {
			lines = lines[len(lines)-maxProgressLines:]
		}
		_ = app.appendProgress(context.Background(), jobID, line)
	}
	if err := scanner.Err(); err != nil {
		_ = app.appendProgress(context.Background(), jobID, "Failed to read evaluator progress: "+redactSecrets(err.Error()))
	}
	return lines
}
