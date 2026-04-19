package main

import (
	"bufio"
	"context"
	"crypto/sha256"
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
	"sort"
	"strings"
	"time"

	"github.com/getsentry/sentry-go"
	"github.com/redis/go-redis/v9"
)

const (
	maxUploadSize    = 5 << 20
	queueKey         = "eval:queue"
	redisResultTTL   = time.Hour
	cacheResultTTL   = 24 * time.Hour
	redisDialTimeout = 5 * time.Second
	workerPopTimeout = 5 * time.Second
	evalTimeout      = 2 * time.Minute
	maxMemoryBytes   = 256
	maxQueueDepth    = 50
	rateLimitMax     = 20
	rateLimitTTL     = time.Hour
	llmRateLimitMax  = 10
	llmRateLimitTTL  = 24 * time.Hour
	maxActiveJobsPerIP = 2
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
		".apk":    true,
		".app":    true,
		".bat":    true,
		".bin":    true,
		".cmd":    true,
		".com":    true,
		".dll":    true,
		".dmg":    true,
		".exe":    true,
		".gadget": true,
		".iso":    true,
		".jar":    true,
		".msi":    true,
		".pkg":    true,
		".ps1":    true,
		".scr":    true,
		".so":     true,
		".vbs":    true,
		".wsf":    true,
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
		{pattern: regexp.MustCompile(`(?i)\b((?:ANTHROPIC|OPENAI)_API_KEY|API[_-]?KEY|TOKEN|SECRET|PASSWORD)\b\s*[:=]\s*['"]?[^'"\s,]+`), replacement: `${1}=[REDACTED]`},
	}
	errResultPending = errors.New("result pending")
	buildVersion     = "dev"
	buildCommit      = "dev"
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
	Status            string         `json:"status"`
	SkillName         string         `json:"skill_name"`
	SkillDescription  string         `json:"skill_description"`
	SkillCompatibility string        `json:"skill_compatibility"`
	SkillContent      string         `json:"skill_content"`
	SupportingContext string         `json:"supporting_context"`
	Deterministic     map[string]any `json:"deterministic"`
	Message           string         `json:"message"`
}

type application struct {
	rdb         *redis.Client
	frontendDir string
	httpClient  *http.Client
}

type evalJob struct {
	JobID        string `json:"jobId"`
	InputDir     string `json:"inputDir"`
	EnableLLM    bool   `json:"enableLlm"`
	LLMRequested bool   `json:"llmRequested,omitempty"`
	LLMProvider  string `json:"llmProvider,omitempty"`
	ClientIP     string `json:"clientIp,omitempty"`
	RequestHash  string `json:"requestHash,omitempty"`
}

type llmAnalysis struct {
	Provider      string   `json:"provider,omitempty"`
	Model         string   `json:"model,omitempty"`
	Strengths     []string `json:"strengths"`
	Weaknesses    []string `json:"weaknesses"`
	Suggestions   []string `json:"suggestions"`
	SecurityFlags []string `json:"security_flags"`
	QualityTier   string   `json:"quality_tier"`
	Mode          string   `json:"mode,omitempty"`
}

type anthropicMessageRequest struct {
	Model     string                   `json:"model"`
	MaxTokens int                      `json:"max_tokens"`
	System    string                   `json:"system,omitempty"`
	Messages  []anthropicMessageRecord `json:"messages"`
}

type anthropicMessageRecord struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type anthropicMessageResponse struct {
	Content []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
	Error *struct {
		Type    string `json:"type"`
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

type openAIChatRequest struct {
	Model     string              `json:"model"`
	MaxTokens int                 `json:"max_tokens,omitempty"`
	Messages  []openAIChatMessage `json:"messages"`
}

type openAIReasoningChatRequest struct {
	Model               string              `json:"model"`
	MaxCompletionTokens int                 `json:"max_completion_tokens,omitempty"`
	Messages            []openAIChatMessage `json:"messages"`
}

type openAIChatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type openAIChatResponse struct {
	Choices []struct {
		Message openAIChatMessage `json:"message"`
	} `json:"choices"`
	Error *struct {
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

type gitHubTarget struct {
	kind     string
	url      string
	owner    string
	repo     string
	ref      string
	path     string
	refPath  string
	fileName string
	htmlURL  string
}

type gitHubDirectoryEntry struct {
	Name        string `json:"name"`
	Path        string `json:"path"`
	Type        string `json:"type"`
	DownloadURL string `json:"download_url"`
}

type statusError struct {
	status  int
	message string
}

func (err statusError) Error() string {
	return err.message
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
		httpClient:  &http.Client{Timeout: 45 * time.Second},
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
	mux.HandleFunc("GET /version", app.handleVersion)
	mux.HandleFunc("POST /upload", app.handleUpload)
	mux.HandleFunc("GET /result/", app.handleResult)
	mux.HandleFunc("GET /about", app.handleAbout)
	mux.HandleFunc("GET /faq", app.handleFAQ)
	mux.HandleFunc("GET /robots.txt", app.handleRobots)
	mux.HandleFunc("GET /sitemap.xml", app.handleSitemap)
	mux.HandleFunc("GET /", app.handleIndex)
	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServer(http.Dir(app.frontendDir))))

	return app.recoverPanics(app.uploadAbuseProtection(mux))
}

func (app *application) handleHealth(w http.ResponseWriter, r *http.Request) {
	status := http.StatusOK
	payload := map[string]any{"status": "ok", "redis": "ok", "version": buildVersion, "commit": buildCommit}
	if err := app.rdb.Ping(r.Context()).Err(); err != nil {
		status = http.StatusServiceUnavailable
		payload["status"] = "degraded"
		payload["redis"] = "error"
		payload["message"] = err.Error()
		captureInfraEvent("redis_healthcheck_failed", err, map[string]string{"component": "redis", "stage": "healthcheck"})
	}
	writeJSON(w, status, payload)
}

func (app *application) handleVersion(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"version": buildVersion, "commit": buildCommit})
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
	llmProvider := normalizeLLMProvider(r.FormValue("llmProvider"))
	if len(files) == 0 && githubURL == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "provide at least one file or a GitHub URL"})
		return
	}
	if len(files) > 0 && githubURL != "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "choose either uploaded files or a GitHub URL"})
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
			writeJSON(w, statusCodeForError(err, http.StatusBadRequest), map[string]string{"error": err.Error()})
			return
		}
	}
	if err := scanUploadedPackage(inputDir); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if enableLLM {
		if llmProvider == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "select an AI provider for optional review"})
			return
		}
		if !isSupportedLLMProvider(llmProvider) {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "unsupported AI provider"})
			return
		}
	}

	requestHash, err := hashEvaluationRequest(inputDir, enableLLM, effectiveLLMProvider(llmProvider))
	if err != nil {
		sentry.CaptureException(err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to fingerprint upload"})
		return
	}
	if cachedJobID, err := app.rdb.Get(r.Context(), redisRequestCacheKey(requestHash)).Result(); err == nil && strings.TrimSpace(cachedJobID) != "" {
		cleanup = false
		_ = os.RemoveAll(inputDir)
		writeJSON(w, http.StatusAccepted, map[string]string{"jobId": strings.TrimSpace(cachedJobID), "cached": "true"})
		return
	} else if err != nil && err != redis.Nil {
		sentry.CaptureException(err)
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "failed to check cached evaluation"})
		return
	}

	jobBytes, err := json.Marshal(evalJob{JobID: jobID, InputDir: inputDir, EnableLLM: enableLLM, LLMRequested: enableLLM, LLMProvider: llmProvider, ClientIP: clientIP(r), RequestHash: requestHash})
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
	consume := parseBool(r.URL.Query().Get("consume"))

	if errorMessage, err := app.rdb.Get(r.Context(), redisErrorKey(jobID)).Result(); err == nil {
		progress := app.getProgress(r.Context(), jobID)
		var structured map[string]any
		if json.Unmarshal([]byte(errorMessage), &structured) == nil {
			structured["progress"] = progress
			writeJSON(w, http.StatusInternalServerError, structured)
			if consume {
				app.releaseJobState(r.Context(), jobID)
			}
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]any{"status": "error", "message": errorMessage, "progress": progress})
		if consume {
			app.releaseJobState(r.Context(), jobID)
		}
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
		if consume {
			app.releaseJobState(r.Context(), jobID)
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(result))
	if consume {
		app.releaseJobState(r.Context(), jobID)
	}
}

func (app *application) handleIndex(w http.ResponseWriter, r *http.Request) {
	http.ServeFile(w, r, filepath.Join(app.frontendDir, "index.html"))
}

func (app *application) handleFAQ(w http.ResponseWriter, r *http.Request) {
	http.ServeFile(w, r, filepath.Join(app.frontendDir, "faq.html"))
}

func (app *application) handleAbout(w http.ResponseWriter, r *http.Request) {
	http.ServeFile(w, r, filepath.Join(app.frontendDir, "about.html"))
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

		enableLLM := parseBool(r.FormValue("enableLlm"))
		if enableLLM {
			llmKey := "ratelimit:llm:" + ip
			llmCount, err := app.rdb.Incr(r.Context(), llmKey).Result()
			if err != nil {
				captureInfraEvent("llm_rate_limit_check_failed", err, map[string]string{"component": "redis", "stage": "llm_rate_limit"})
				writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "llm rate limit check failed"})
				return
			}
			if llmCount == 1 {
				if err := app.rdb.Expire(r.Context(), llmKey, llmRateLimitTTL).Err(); err != nil {
					captureInfraEvent("llm_rate_limit_expire_failed", err, map[string]string{"component": "redis", "stage": "llm_rate_limit_expire"})
				}
			}
			if llmCount > llmRateLimitMax {
				writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "optional AI review rate limit exceeded"})
				return
			}
		}

		activeKey := redisActiveJobsKey(ip)
		activeCount, err := app.rdb.Get(r.Context(), activeKey).Int64()
		if err != nil && err != redis.Nil {
			captureInfraEvent("active_job_check_failed", err, map[string]string{"component": "redis", "stage": "active_job_check"})
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "active job check failed"})
			return
		}
		if activeCount >= maxActiveJobsPerIP {
			writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "too many active evaluations from this IP"})
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
		app.decrementActiveJob(parent, job.ClientIP)
	}()
	app.incrementActiveJob(parent, job.ClientIP)
	_ = app.appendProgress(parent, job.JobID, "Worker picked up job.")

	ctx, cancel := context.WithTimeout(parent, evalTimeout)
	defer cancel()
	_ = app.appendProgress(parent, job.JobID, "Starting evaluator...")
	cmd := exec.CommandContext(ctx, "python3", "/app/eval/run_eval.py")
	cmd.Dir = "/app"
	cmd.Env = append(os.Environ(), "INPUT_DIR="+job.InputDir)
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
		captureInfraEvent("eval_failed", err, map[string]string{"component": "worker", "stage": "eval_wait"})
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
		return
	}
	if job.RequestHash != "" {
		if err := app.rdb.Set(parent, redisRequestCacheKey(job.RequestHash), job.JobID, cacheResultTTL).Err(); err != nil {
			captureInfraEvent("request_cache_store_failed", err, map[string]string{"component": "redis", "stage": "request_cache_store"})
		}
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
		provider := effectiveLLMProvider(job.LLMProvider)
		if !llmProviderConfigured(provider) {
			_ = app.appendProgress(ctx, job.JobID, "Optional LLM review requested, but the selected provider is not configured. Returning deterministic result only.")
		} else {
			_ = app.appendProgress(ctx, job.JobID, "LLM evaluation...")
			generated, err := app.runLLMReview(ctx, provider, container)
			if err != nil {
				_ = app.appendProgress(ctx, job.JobID, "Optional LLM review failed. Returning deterministic result only.")
				captureInfraEvent("llm_review_failed", err, map[string]string{"component": "llm", "provider": provider})
			} else {
				analysis = &generated
				_ = app.appendProgress(ctx, job.JobID, "Optional LLM review completed.")
			}
		}
	}

	result := map[string]any{
		"status":              "ok",
		"skill_name":          container.SkillName,
		"skill_description":   container.SkillDescription,
		"skill_compatibility": container.SkillCompatibility,
		"overall_score":       computeOverallScore(container.Deterministic, analysis),
		"overall_tier":        computeOverallTier(container.Deterministic, analysis),
		"summary":             summarizeIssues(container.Deterministic, analysis),
		"deterministic":       container.Deterministic,
		"llm_enabled":         job.EnableLLM,
		"llm_requested":       job.LLMRequested,
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

func computeOverallTier(deterministic map[string]any, analysis *llmAnalysis) string {
	if analysis != nil {
		return normalizeQualityTier(analysis.QualityTier)
	}
	return overallTierFromScore(computeOverallScore(deterministic, nil))
}

func overallTierFromScore(score int) string {
	switch {
	case score >= 90:
		return "excellent"
	case score >= 75:
		return "good"
	case score >= 50:
		return "needs_work"
	default:
		return "poor"
	}
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

func llmProviderConfigured(provider string) bool {
	switch effectiveLLMProvider(provider) {
	case "anthropic":
		return strings.TrimSpace(os.Getenv("ANTHROPIC_API_KEY")) != ""
	case "openai":
		return strings.TrimSpace(os.Getenv("OPENAI_API_KEY")) != ""
	default:
		return false
	}
}

func effectiveLLMProvider(provider string) string {
	provider = normalizeLLMProvider(provider)
	if provider == "" {
		provider = normalizeLLMProvider(os.Getenv("LLM_PROVIDER"))
	}
	if provider == "" {
		return "anthropic"
	}
	return provider
}

func normalizeLLMProvider(provider string) string {
	return strings.TrimSpace(strings.ToLower(provider))
}

func isSupportedLLMProvider(provider string) bool {
	switch normalizeLLMProvider(provider) {
	case "anthropic", "openai":
		return true
	default:
		return false
	}
}

func llmModelForProvider(provider string) string {
	switch effectiveLLMProvider(provider) {
	case "openai":
		return getenvDefault("OPENAI_MODEL", "gpt-4.1")
	default:
		return getenvDefault("ANTHROPIC_MODEL", "claude-sonnet-4-6")
	}
}

func llmMaxTokensForProvider(provider string) int {
	switch effectiveLLMProvider(provider) {
	case "openai":
		return parseIntDefault(os.Getenv("OPENAI_MAX_TOKENS"), 1200)
	default:
		return parseIntDefault(os.Getenv("ANTHROPIC_MAX_TOKENS"), 1200)
	}
}

func (app *application) runLLMReview(ctx context.Context, provider string, container evalContainerResult) (llmAnalysis, error) {
	provider = effectiveLLMProvider(provider)
	systemPrompt := llmSystemPrompt()
	userPrompt := buildLLMPrompt(container)

	var (
		analysis llmAnalysis
		err     error
	)
	switch provider {
	case "openai":
		analysis, err = app.runOpenAIReview(ctx, systemPrompt, userPrompt)
	default:
		analysis, err = app.runAnthropicReview(ctx, systemPrompt, userPrompt)
	}
	if err != nil {
		return llmAnalysis{}, err
	}
	analysis.Provider = provider
	analysis.Model = llmModelForProvider(provider)
	analysis.Mode = "opt_in"
	analysis.QualityTier = normalizeQualityTier(analysis.QualityTier)
	analysis.Strengths = uniqueStrings(analysis.Strengths)
	analysis.Weaknesses = uniqueStrings(analysis.Weaknesses)
	analysis.Suggestions = uniqueStrings(analysis.Suggestions)
	analysis.SecurityFlags = uniqueStrings(analysis.SecurityFlags)
	return analysis, nil
}

func (app *application) runAnthropicReview(ctx context.Context, systemPrompt, userPrompt string) (llmAnalysis, error) {
	reqBody := anthropicMessageRequest{
		Model:     llmModelForProvider("anthropic"),
		MaxTokens: llmMaxTokensForProvider("anthropic"),
		System:    systemPrompt,
		Messages: []anthropicMessageRecord{{
			Role:    "user",
			Content: userPrompt,
		}},
	}
	body, err := json.Marshal(reqBody)
	if err != nil {
		return llmAnalysis{}, errors.New("failed to encode anthropic request")
	}
	endpoint := getenvDefault("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1/messages")
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, strings.NewReader(string(body)))
	if err != nil {
		return llmAnalysis{}, errors.New("failed to create anthropic request")
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", strings.TrimSpace(os.Getenv("ANTHROPIC_API_KEY")))
	req.Header.Set("anthropic-version", "2023-06-01")
	respBody, err := app.doLLMRequest("anthropic", req)
	if err != nil {
		return llmAnalysis{}, err
	}
	return parseAnthropicAnalysis(respBody)
}

func (app *application) runOpenAIReview(ctx context.Context, systemPrompt, userPrompt string) (llmAnalysis, error) {
	reqBody, err := buildOpenAIRequestBody(llmModelForProvider("openai"), llmMaxTokensForProvider("openai"), systemPrompt, userPrompt)
	if err != nil {
		return llmAnalysis{}, err
	}
	body, err := json.Marshal(reqBody)
	if err != nil {
		return llmAnalysis{}, errors.New("failed to encode openai request")
	}
	endpoint := getenvDefault("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions")
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, strings.NewReader(string(body)))
	if err != nil {
		return llmAnalysis{}, errors.New("failed to create openai request")
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(os.Getenv("OPENAI_API_KEY")))
	respBody, err := app.doLLMRequest("openai", req)
	if err != nil {
		return llmAnalysis{}, wrapProviderError("openai", err)
	}
	return parseOpenAIAnalysis(respBody)
}

func buildOpenAIRequestBody(model string, maxTokens int, systemPrompt, userPrompt string) (any, error) {
	messages := []openAIChatMessage{{Role: "system", Content: systemPrompt}, {Role: "user", Content: userPrompt}}
	switch openAIRequestMode(model) {
	case "chat_max_completion_tokens":
		return openAIReasoningChatRequest{
			Model:               model,
			MaxCompletionTokens: maxTokens,
			Messages:            messages,
		}, nil
	case "chat_max_tokens":
		return openAIChatRequest{
			Model:     model,
			MaxTokens: maxTokens,
			Messages:  messages,
		}, nil
	default:
		return nil, fmt.Errorf("unsupported openai model: %s", model)
	}
}

func openAIRequestMode(model string) string {
	model = strings.TrimSpace(strings.ToLower(model))
	switch {
	case strings.HasPrefix(model, "gpt-5.4"), strings.HasPrefix(model, "gpt-5.3-chat-latest"):
		return "chat_max_completion_tokens"
	case strings.HasPrefix(model, "gpt-4.1"):
		return "chat_max_tokens"
	default:
		return "chat_max_tokens"
	}
}

func (app *application) doLLMRequest(provider string, req *http.Request) ([]byte, error) {
	client := app.httpClient
	if client == nil {
		client = &http.Client{Timeout: 45 * time.Second}
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()
	limited := io.LimitReader(resp.Body, 1<<20)
	respBody, err := io.ReadAll(limited)
	if err != nil {
		return nil, errors.New("failed to read provider response")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		switch provider {
		case "anthropic":
			return nil, statusError{status: resp.StatusCode, message: parseAnthropicError(respBody, resp.Status)}
		case "openai":
			return nil, statusError{status: resp.StatusCode, message: parseOpenAIError(respBody, resp.Status)}
		default:
			return nil, statusError{status: resp.StatusCode, message: redactSecrets(strings.TrimSpace(string(respBody)))}
		}
	}
	return respBody, nil
}

func wrapProviderError(provider string, err error) error {
	if err == nil {
		return nil
	}
	var statusErr statusError
	if errors.As(err, &statusErr) {
		return errors.New(statusErr.message)
	}
	return errors.New(provider + " request failed: " + err.Error())
}

func llmSystemPrompt() string {
	return strings.TrimSpace(`You are evaluating an uploaded SKILL.md package.
Return exactly one JSON object and nothing else.
Do not wrap the JSON in markdown fences.
Use this schema exactly:
{
  "strengths": ["string"],
  "weaknesses": ["string"],
  "suggestions": ["string"],
  "security_flags": ["string"],
  "quality_tier": "excellent|good|needs_work|poor"
}
Keep each list concise. Base the review on the uploaded content and deterministic findings provided by the user message.`)
}

func buildLLMPrompt(container evalContainerResult) string {
	deterministicJSON, _ := json.Marshal(container.Deterministic)
	skillContent := truncateForLLM(container.SkillContent, 40000)
	supporting := truncateForLLM(container.SupportingContext, 12000)
	return strings.TrimSpace("Evaluate this skill package. Focus on portability, clarity, completeness, examples, failure handling, and security posture. Use the deterministic findings as hard constraints.\n\nDeterministic findings:\n" + string(deterministicJSON) + "\n\nPrimary SKILL.md content:\n" + skillContent + "\n\nSupporting context:\n" + supporting)
}

func parseAnthropicAnalysis(body []byte) (llmAnalysis, error) {
	var response anthropicMessageResponse
	if err := json.Unmarshal(body, &response); err != nil {
		return llmAnalysis{}, errors.New("failed to decode anthropic response")
	}
	text := strings.TrimSpace(joinAnthropicText(response.Content))
	if text == "" {
		return llmAnalysis{}, errors.New("anthropic response did not contain text")
	}
	text = strings.TrimSpace(strings.TrimPrefix(strings.TrimSuffix(text, "```"), "```json"))
	var analysis llmAnalysis
	if err := json.Unmarshal([]byte(text), &analysis); err != nil {
		return llmAnalysis{}, errors.New("anthropic response did not return valid JSON")
	}
	return analysis, nil
}

func parseOpenAIAnalysis(body []byte) (llmAnalysis, error) {
	var response openAIChatResponse
	if err := json.Unmarshal(body, &response); err != nil {
		return llmAnalysis{}, errors.New("failed to decode openai response")
	}
	if response.Error != nil && strings.TrimSpace(response.Error.Message) != "" {
		return llmAnalysis{}, errors.New("openai request failed: " + redactSecrets(response.Error.Message))
	}
	if len(response.Choices) == 0 || strings.TrimSpace(response.Choices[0].Message.Content) == "" {
		return llmAnalysis{}, errors.New("openai response did not contain text")
	}
	return parseLLMJSONPayload(strings.TrimSpace(response.Choices[0].Message.Content), "openai")
}

func parseLLMJSONPayload(text, provider string) (llmAnalysis, error) {
	text = strings.TrimSpace(strings.TrimPrefix(strings.TrimSuffix(strings.TrimSpace(text), "```"), "```json"))
	var analysis llmAnalysis
	if err := json.Unmarshal([]byte(text), &analysis); err != nil {
		return llmAnalysis{}, fmt.Errorf("%s response did not return valid JSON", provider)
	}
	return analysis, nil
}

func joinAnthropicText(parts []struct {
	Type string `json:"type"`
	Text string `json:"text"`
}) string {
	segments := make([]string, 0, len(parts))
	for _, part := range parts {
		if part.Type == "text" && strings.TrimSpace(part.Text) != "" {
			segments = append(segments, part.Text)
		}
	}
	return strings.Join(segments, "\n")
}

func parseAnthropicError(body []byte, fallback string) string {
	var response anthropicMessageResponse
	if json.Unmarshal(body, &response) == nil && response.Error != nil && strings.TrimSpace(response.Error.Message) != "" {
		return "anthropic request failed: " + redactSecrets(response.Error.Message)
	}
	trimmed := strings.TrimSpace(string(body))
	if trimmed == "" {
		return "anthropic request failed: " + fallback
	}
	return "anthropic request failed: " + redactSecrets(trimmed)
}

func parseOpenAIError(body []byte, fallback string) string {
	var response openAIChatResponse
	if json.Unmarshal(body, &response) == nil && response.Error != nil && strings.TrimSpace(response.Error.Message) != "" {
		return "openai request failed: " + redactSecrets(response.Error.Message)
	}
	trimmed := strings.TrimSpace(string(body))
	if trimmed == "" {
		return "openai request failed: " + fallback
	}
	return "openai request failed: " + redactSecrets(trimmed)
}

func truncateForLLM(text string, maxChars int) string {
	text = strings.TrimSpace(text)
	if len(text) <= maxChars {
		return text
	}
	return text[:maxChars] + "\n[truncated]"
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
	switch normalizeQualityTier(tier) {
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

func normalizeQualityTier(tier string) string {
	switch strings.TrimSpace(strings.ToLower(tier)) {
	case "excellent":
		return "excellent"
	case "good":
		return "good"
	case "poor":
		return "poor"
	default:
		return "needs_work"
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
		return statusError{status: http.StatusBadRequest, message: err.Error()}
	}
	if target.hasAmbiguousRefPath() {
		target, err = resolveGitHubTargetRef(ctx, target)
		if err != nil {
			return err
		}
	}
	if target.kind == "directory" {
		return fetchGitHubDirectory(ctx, target, rootDir)
	}
	return fetchGitHubResolvedFile(ctx, target, rootDir)
}


func fetchGitHubResolvedFile(ctx context.Context, target gitHubTarget, rootDir string) error {
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
		return gitHubUpstreamError(err, "failed to fetch GitHub URL")
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return gitHubHTTPError(resp.StatusCode, resp.Status)
	}

	limited := io.LimitReader(resp.Body, githubFetchLimit+1)
	content, err := io.ReadAll(limited)
	if err != nil {
		return statusError{status: http.StatusBadGateway, message: "failed to read GitHub file"}
	}
	if len(content) > githubFetchLimit {
		return statusError{status: http.StatusBadRequest, message: "GitHub file exceeds 5MB limit"}
	}

	mimeType := http.DetectContentType(content)
	if headerType := resp.Header.Get("Content-Type"); headerType != "" {
		if parsed, _, parseErr := mime.ParseMediaType(headerType); parseErr == nil && parsed != "application/octet-stream" {
			mimeType = parsed
		}
	}
	if err := validateFileType(target.fileName, mimeType); err != nil {
		return statusError{status: http.StatusBadRequest, message: err.Error()}
	}

	destination := filepath.Join(rootDir, filepath.FromSlash(target.path))
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return statusError{status: http.StatusInternalServerError, message: fmt.Sprintf("failed to prepare %s", target.fileName)}
	}
	if err := os.WriteFile(destination, content, 0o644); err != nil {
		return statusError{status: http.StatusInternalServerError, message: fmt.Sprintf("failed to save %s", target.fileName)}
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
		candidatePath := strings.Join(parts[3:], "/")
		filename := filepath.Base(candidatePath)
		if filename == "." || filename == "/" || filename == "" {
			return gitHubTarget{}, errors.New("GitHub raw URL must point to a file")
		}
		return hydrateGitHubTarget(gitHubTarget{kind: "file", owner: parts[0], repo: parts[1], ref: parts[2], path: candidatePath, refPath: strings.Join(parts[2:], "/")})
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
		candidatePath := strings.Join(parts[4:], "/")
		if candidatePath == "" {
			return gitHubTarget{}, errors.New("GitHub tree URL must point to a directory")
		}
		return hydrateGitHubTarget(gitHubTarget{kind: "directory", owner: parts[0], repo: parts[1], ref: parts[3], path: candidatePath, refPath: strings.Join(parts[3:], "/")})
	}

	ref, path, err := splitGitHubBlobPath(parts)
	if err != nil {
		return gitHubTarget{}, err
	}

	filename := filepath.Base(path)
	if filename == "." || filename == "/" || filename == "" {
		return gitHubTarget{}, errors.New("GitHub URL must point to a file")
	}
	return hydrateGitHubTarget(gitHubTarget{kind: "file", owner: parts[0], repo: parts[1], ref: ref, path: path, refPath: strings.Join(parts[3:], "/")})
}

func splitGitHubBlobPath(parts []string) (string, string, error) {
	if len(parts) < 5 {
		return "", "", errors.New("GitHub blob URL must include a branch and path")
	}
	return parts[3], strings.Join(parts[4:], "/"), nil
}

func (target gitHubTarget) hasAmbiguousRefPath() bool {
	segments := strings.Split(strings.Trim(target.refPath, "/"), "/")
	return len(segments) >= 3
}

func hydrateGitHubTarget(target gitHubTarget) (gitHubTarget, error) {
	if strings.TrimSpace(target.owner) == "" || strings.TrimSpace(target.repo) == "" {
		return gitHubTarget{}, errors.New("invalid GitHub URL")
	}
	target.ref = strings.Trim(strings.TrimSpace(target.ref), "/")
	target.path = strings.Trim(strings.TrimSpace(target.path), "/")
	if target.ref == "" || target.path == "" {
		switch target.kind {
		case "directory":
			return gitHubTarget{}, errors.New("GitHub tree URL must include a branch and path")
		default:
			return gitHubTarget{}, errors.New("GitHub URL must point to a file")
		}
	}
	if target.refPath == "" {
		target.refPath = strings.Join([]string{target.ref, target.path}, "/")
	}
	switch target.kind {
	case "directory":
		target.htmlURL = (&urlpkg.URL{Scheme: "https", Host: "github.com", Path: "/" + strings.Join([]string{target.owner, target.repo, "tree", target.ref, target.path}, "/")}).String()
		target.url = ""
		target.fileName = ""
	case "file":
		target.fileName = filepath.Base(target.path)
		if target.fileName == "." || target.fileName == "/" || target.fileName == "" {
			return gitHubTarget{}, errors.New("GitHub URL must point to a file")
		}
		target.url = (&urlpkg.URL{Scheme: "https", Host: "raw.githubusercontent.com", Path: "/" + strings.Join([]string{target.owner, target.repo, target.ref, target.path}, "/")}).String()
		target.htmlURL = ""
	default:
		return gitHubTarget{}, errors.New("invalid GitHub URL")
	}
	return target, nil
}

func resolveGitHubTargetRef(ctx context.Context, target gitHubTarget) (gitHubTarget, error) {
	segments := strings.Split(strings.Trim(target.refPath, "/"), "/")
	if len(segments) < 2 {
		return gitHubTarget{}, statusError{status: http.StatusBadRequest, message: "GitHub URL could not be found"}
	}
	for split := len(segments) - 1; split >= 1; split-- {
		candidateRef := strings.Join(segments[:split], "/")
		candidatePath := strings.Join(segments[split:], "/")
		kind, err := fetchGitHubContentKind(ctx, target.owner, target.repo, candidateRef, candidatePath)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			return gitHubTarget{}, err
		}
		if target.kind == "directory" && kind != "dir" {
			continue
		}
		if target.kind == "file" && kind != "file" {
			continue
		}
		return hydrateGitHubTarget(gitHubTarget{
			kind:    target.kind,
			owner:   target.owner,
			repo:    target.repo,
			ref:     candidateRef,
			path:    candidatePath,
			refPath: target.refPath,
		})
	}
	return gitHubTarget{}, statusError{status: http.StatusBadRequest, message: "GitHub URL could not be found"}
}

func fetchGitHubContentKind(ctx context.Context, owner, repo, ref, targetPath string) (string, error) {
	apiURL := &urlpkg.URL{
		Scheme: "https",
		Host:   "api.github.com",
		Path:   "/repos/" + owner + "/" + repo + "/contents/" + escapeGitHubContentPath(targetPath),
	}
	query := apiURL.Query()
	query.Set("ref", ref)
	apiURL.RawQuery = query.Encode()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL.String(), nil)
	if err != nil {
		return "", statusError{status: http.StatusInternalServerError, message: "failed to create GitHub metadata request"}
	}
	req.Header.Set("User-Agent", "agents-skill-eval")
	req.Header.Set("Accept", "application/vnd.github+json")
	if token := strings.TrimSpace(os.Getenv("GITHUB_TOKEN")); token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}

	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", gitHubUpstreamError(err, "failed to resolve GitHub URL")
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return "", os.ErrNotExist
	}
	if resp.StatusCode != http.StatusOK {
		return "", gitHubHTTPError(resp.StatusCode, resp.Status)
	}

	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return "", statusError{status: http.StatusBadGateway, message: "failed to read GitHub metadata"}
	}
	var payload any
	if err := json.Unmarshal(body, &payload); err != nil {
		return "", statusError{status: http.StatusBadGateway, message: "failed to decode GitHub metadata"}
	}
	switch value := payload.(type) {
	case []any:
		return "dir", nil
	case map[string]any:
		kind, _ := value["type"].(string)
		if kind == "dir" || kind == "file" {
			return kind, nil
		}
	}
	return "", statusError{status: http.StatusBadGateway, message: "failed to decode GitHub metadata"}
}

func escapeGitHubContentPath(value string) string {
	parts := strings.Split(strings.Trim(value, "/"), "/")
	for i, part := range parts {
		parts[i] = urlpkg.PathEscape(part)
	}
	return strings.Join(parts, "/")
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
		return statusError{status: http.StatusBadRequest, message: "GitHub directory must contain SKILL.md or skill.md"}
	}
	for _, entry := range selected {
		fileTarget, err := hydrateGitHubTarget(gitHubTarget{kind: "file", owner: target.owner, repo: target.repo, ref: target.ref, path: entry.Path})
		if err != nil {
			return statusError{status: http.StatusBadRequest, message: err.Error()}
		}
		if err := fetchGitHubResolvedFile(ctx, fileTarget, rootDir); err != nil {
			return err
		}
	}
	return nil
}

func fetchGitHubDirectoryEntries(ctx context.Context, target gitHubTarget) ([]gitHubDirectoryEntry, error) {
	apiURL := fmt.Sprintf("https://api.github.com/repos/%s/%s/git/trees/%s?recursive=1", target.owner, target.repo, urlpkg.PathEscape(target.ref))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, apiURL, nil)
	if err != nil {
		return nil, statusError{status: http.StatusInternalServerError, message: "failed to create GitHub directory request"}
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
		return nil, gitHubHTTPError(resp.StatusCode, resp.Status)
	}
	var tree struct {
		Tree []struct {
			Path string `json:"path"`
			Type string `json:"type"`
		} `json:"tree"`
		Truncated bool `json:"truncated"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&tree); err != nil {
		return nil, statusError{status: http.StatusBadGateway, message: "failed to decode GitHub directory listing"}
	}
	if tree.Truncated {
		return nil, statusError{status: http.StatusBadRequest, message: "GitHub directory is too large to evaluate recursively"}
	}
	prefix := strings.Trim(strings.TrimSpace(target.path), "/")
	if prefix == "" {
		return nil, statusError{status: http.StatusBadRequest, message: "GitHub directory path is empty"}
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
		return nil, statusError{status: http.StatusBadRequest, message: "failed to discover files from GitHub directory"}
	}
	return entries, nil
}

func fetchGitHubDirectoryEntriesHTML(ctx context.Context, target gitHubTarget) ([]gitHubDirectoryEntry, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target.htmlURL, nil)
	if err != nil {
		return nil, statusError{status: http.StatusInternalServerError, message: "failed to create GitHub directory request"}
	}
	req.Header.Set("User-Agent", "agents-skill-eval")
	req.Header.Set("Accept", "text/html")
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, gitHubUpstreamError(err, "failed to fetch GitHub directory")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, gitHubHTTPError(resp.StatusCode, resp.Status)
	}
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, statusError{status: http.StatusBadGateway, message: "failed to read GitHub directory page"}
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
		return nil, statusError{status: http.StatusBadRequest, message: "failed to discover files from GitHub directory page"}
	}
	return entries, nil
}

func statusCodeForError(err error, fallback int) int {
	var target statusError
	if errors.As(err, &target) && target.status != 0 {
		return target.status
	}
	return fallback
}

func gitHubUpstreamError(err error, fallback string) error {
	message := fallback
	if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
		message = "GitHub request timed out"
	}
	return statusError{status: http.StatusBadGateway, message: message}
}

func gitHubHTTPError(statusCode int, status string) error {
	switch {
	case statusCode == http.StatusNotFound:
		return statusError{status: http.StatusBadRequest, message: "GitHub URL could not be found"}
	case statusCode == http.StatusUnauthorized:
		return statusError{status: http.StatusBadGateway, message: "GitHub rejected the request"}
	case statusCode == http.StatusForbidden || statusCode == http.StatusTooManyRequests:
		return statusError{status: http.StatusServiceUnavailable, message: "GitHub is temporarily unavailable"}
	case statusCode >= 500:
		return statusError{status: http.StatusBadGateway, message: "GitHub returned " + status}
	default:
		return statusError{status: http.StatusBadRequest, message: "GitHub URL returned " + status}
	}
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

func redisRequestCacheKey(requestHash string) string {
	return "requestcache:" + requestHash
}

func redisActiveJobsKey(ip string) string {
	return "activejobs:" + strings.TrimSpace(ip)
}

func (app *application) releaseJobState(ctx context.Context, jobID string) {
	if app == nil || app.rdb == nil {
		return
	}
	if err := app.rdb.Del(ctx, redisResultKey(jobID), redisErrorKey(jobID), redisProgressKey(jobID)).Err(); err != nil && err != redis.Nil {
		captureInfraEvent("job_state_release_failed", err, map[string]string{"component": "redis", "stage": "result_release"})
	}
}

func clientIP(r *http.Request) string {
	trustedProxy := isTrustedProxy(r.RemoteAddr)
	if trustedProxy {
		if forwarded := r.Header.Get("X-Forwarded-For"); forwarded != "" {
			parts := strings.Split(forwarded, ",")
			return strings.TrimSpace(parts[0])
		}
		if realIP := r.Header.Get("X-Real-IP"); realIP != "" {
			return realIP
		}
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err == nil {
		return host
	}
	return r.RemoteAddr
}

func isTrustedProxy(remoteAddr string) bool {
	host, _, err := net.SplitHostPort(remoteAddr)
	if err != nil {
		host = remoteAddr
	}
	parsed := net.ParseIP(strings.TrimSpace(host))
	if parsed == nil {
		return false
	}
	return parsed.IsLoopback()
}

func hashEvaluationRequest(rootDir string, enableLLM bool, provider string) (string, error) {
	providerValue := "none"
	if enableLLM {
		providerValue = effectiveLLMProvider(provider)
	}
	entries := []string{fmt.Sprintf("llm=%t", enableLLM), "provider=" + providerValue}
	err := filepath.WalkDir(rootDir, func(current string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(rootDir, current)
		if err != nil {
			return err
		}
		content, err := os.ReadFile(current)
		if err != nil {
			return err
		}
		fileHash := sha256.Sum256(content)
		entries = append(entries, filepath.ToSlash(rel)+":"+hex.EncodeToString(fileHash[:]))
		return nil
	})
	if err != nil {
		return "", err
	}
	sort.Strings(entries)
	digest := sha256.Sum256([]byte(strings.Join(entries, "\n")))
	return hex.EncodeToString(digest[:]), nil
}

func (app *application) incrementActiveJob(ctx context.Context, ip string) {
	if strings.TrimSpace(ip) == "" || app == nil || app.rdb == nil {
		return
	}
	key := redisActiveJobsKey(ip)
	if err := app.rdb.Incr(ctx, key).Err(); err == nil {
		_ = app.rdb.Expire(ctx, key, redisResultTTL).Err()
	}
}

func (app *application) decrementActiveJob(ctx context.Context, ip string) {
	if strings.TrimSpace(ip) == "" || app == nil || app.rdb == nil {
		return
	}
	key := redisActiveJobsKey(ip)
	count, err := app.rdb.Decr(ctx, key).Result()
	if err != nil {
		return
	}
	if count <= 0 {
		_ = app.rdb.Del(ctx, key).Err()
	}
	return
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
