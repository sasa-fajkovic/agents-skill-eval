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
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/getsentry/sentry-go"
	"github.com/oschwald/maxminddb-golang"
	"github.com/redis/go-redis/v9"
)

const (
	maxUploadSize    = 5 << 20
	queueKey         = "eval:queue"
	redisResultTTL   = time.Hour
	redisDialTimeout = 5 * time.Second
	workerPopTimeout = 5 * time.Second
	dockerTimeout    = 30 * time.Second
	maxMemoryBytes   = 256
	maxQueueDepth    = 50
	rateLimitMax     = 5
	rateLimitTTL     = time.Hour
	maxmindDBPath    = "/etc/maxmind/GeoLite2-ASN.mmdb"
	githubFetchLimit = 5 << 20
	maxProgressLines = 200
)

var (
	allowedMimeTypes = map[string]bool{
		"text/plain":         true,
		"text/markdown":      true,
		"application/pdf":    true,
		"image/png":          true,
		"image/jpeg":         true,
		"application/json":   true,
		"text/x-python":      true,
		"text/x-shellscript": true,
	}
	blockedASNs = map[uint]bool{
		16509: true,
		15169: true,
		8075:  true,
		24940: true,
		14061: true,
		47583: true,
		20473: true,
		63949: true,
	}
	errResultPending = errors.New("result pending")
)

type application struct {
	rdb          *redis.Client
	frontendDir  string
	maxmindDB    *maxminddb.Reader
	maxmindOnce  sync.Once
	maxmindError error
}

type evalJob struct {
	JobID    string `json:"jobId"`
	InputDir string `json:"inputDir"`
}

type maxmindASNRecord struct {
	AutonomousSystemNumber uint   `maxminddb:"autonomous_system_number"`
	AutonomousSystemOrg    string `maxminddb:"autonomous_system_organization"`
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
	Name string
	Type string
}

func main() {
	if err := sentry.Init(sentry.ClientOptions{Dsn: os.Getenv("SENTRY_DSN")}); err != nil {
		log.Printf("sentry init failed: %v", err)
	}
	defer sentry.Flush(2 * time.Second)

	ctx := context.Background()
	redisAddr := getenvDefault("REDIS_ADDR", "127.0.0.1:6379")
	rdb := redis.NewClient(&redis.Options{
		Addr:        redisAddr,
		DialTimeout: redisDialTimeout,
	})
	if err := rdb.Ping(ctx).Err(); err != nil {
		sentry.CaptureException(err)
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
		sentry.CaptureException(err)
		log.Fatal(err)
	}
}

func (app *application) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", app.handleHealth)
	mux.HandleFunc("POST /upload", app.handleUpload)
	mux.HandleFunc("GET /result/", app.handleResult)
	mux.HandleFunc("GET /robots.txt", app.handleRobots)
	mux.HandleFunc("GET /sitemap.xml", app.handleSitemap)
	mux.HandleFunc("GET /", app.handleIndex)
	mux.Handle("GET /static/", http.StripPrefix("/static/", http.FileServer(http.Dir(app.frontendDir))))

	return app.recoverPanics(app.asnBlocker(app.uploadAbuseProtection(mux)))
}

func (app *application) handleHealth(w http.ResponseWriter, r *http.Request) {
	status := http.StatusOK
	payload := map[string]any{"status": "ok", "redis": "ok"}
	if err := app.rdb.Ping(r.Context()).Err(); err != nil {
		status = http.StatusServiceUnavailable
		payload["status"] = "degraded"
		payload["redis"] = "error"
		payload["message"] = err.Error()
		sentry.CaptureException(err)
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

	jobBytes, err := json.Marshal(evalJob{JobID: jobID, InputDir: inputDir})
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
				sentry.CaptureException(err)
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
			sentry.CaptureException(err)
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
			sentry.CaptureException(err)
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "rate limit check failed"})
			return
		}
		if count == 1 {
			if err := app.rdb.Expire(r.Context(), key, rateLimitTTL).Err(); err != nil {
				sentry.CaptureException(err)
			}
		}
		if count > rateLimitMax {
			writeJSON(w, http.StatusTooManyRequests, map[string]string{"error": "rate limit exceeded"})
			return
		}

		next.ServeHTTP(w, r)
	})
}

func (app *application) asnBlocker(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reader, err := app.maxmindReader()
		if err != nil || reader == nil {
			next.ServeHTTP(w, r)
			return
		}

		parsedIP := net.ParseIP(clientIP(r))
		if parsedIP == nil {
			next.ServeHTTP(w, r)
			return
		}

		var record maxmindASNRecord
		if lookupErr := reader.Lookup(parsedIP, &record); lookupErr != nil {
			sentry.CaptureException(lookupErr)
			next.ServeHTTP(w, r)
			return
		}
		if blockedASNs[record.AutonomousSystemNumber] {
			writeJSON(w, http.StatusForbidden, map[string]string{"error": "request blocked"})
			return
		}

		next.ServeHTTP(w, r)
	})
}

func (app *application) maxmindReader() (*maxminddb.Reader, error) {
	app.maxmindOnce.Do(func() {
		if _, err := os.Stat(maxmindDBPath); err != nil {
			app.maxmindError = err
			return
		}
		reader, err := maxminddb.Open(maxmindDBPath)
		if err != nil {
			app.maxmindError = err
			return
		}
		app.maxmindDB = reader
	})
	return app.maxmindDB, app.maxmindError
}

func (app *application) workerLoop(parent context.Context) {
	for {
		result, err := app.rdb.BRPop(parent, workerPopTimeout, queueKey).Result()
		if err == redis.Nil {
			continue
		}
		if err != nil {
			sentry.CaptureException(err)
			log.Printf("worker pop failed: %v", err)
			time.Sleep(time.Second)
			continue
		}
		if len(result) < 2 {
			continue
		}

		var job evalJob
		if err := json.Unmarshal([]byte(result[1]), &job); err != nil {
			sentry.CaptureException(err)
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
	dockerNetwork := getenvDefault("EVAL_DOCKER_NETWORK", "bridge")
	dockerImage := getenvDefault("EVAL_DOCKER_IMAGE", "ghcr.io/sasa-fajkovic/agents-skill-eval:latest")
	_ = app.appendProgress(parent, job.JobID, "Starting isolated evaluator container...")

	args := []string{
		"run",
		"--rm",
		"--read-only",
		"--tmpfs", "/tmp:size=50m",
		"--memory", fmt.Sprintf("%dm", maxMemoryBytes),
		"--cpus", "0.5",
		"--pids-limit", "50",
		"--security-opt", "no-new-privileges",
		"--user", "1001:1001",
		"-v", job.InputDir+":/input:ro",
		"-e", "ANTHROPIC_API_KEY="+os.Getenv("ANTHROPIC_API_KEY"),
		dockerImage,
	}
	if dockerNetwork != "" {
		args = append([]string{"run", "--rm", "--network", dockerNetwork}, args[2:]...)
	}

	cmd := exec.CommandContext(ctx, "docker", args...)
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		sentry.CaptureException(err)
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), err.Error(), redisResultTTL).Err()
		return
	}
	stderrPipe, err := cmd.StderrPipe()
	if err != nil {
		sentry.CaptureException(err)
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), err.Error(), redisResultTTL).Err()
		return
	}
	if err := cmd.Start(); err != nil {
		sentry.CaptureException(err)
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), err.Error(), redisResultTTL).Err()
		return
	}

	stdoutCh := make(chan string, 1)
	stderrCh := make(chan []string, 1)
	go func() {
		bytes, _ := io.ReadAll(stdoutPipe)
		stdoutCh <- strings.TrimSpace(string(bytes))
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
		sentry.CaptureException(err)
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), message, redisResultTTL).Err()
		return
	}

	_ = app.appendProgress(parent, job.JobID, "Evaluation completed.")
	trimmed := stdoutOutput
	if trimmed == "" {
		trimmed = `{"status":"error","message":"empty evaluation output"}`
	}
	if err := app.rdb.Set(parent, redisResultKey(job.JobID), trimmed, redisResultTTL).Err(); err != nil {
		sentry.CaptureException(err)
		_ = app.rdb.Set(parent, redisErrorKey(job.JobID), err.Error(), redisResultTTL).Err()
	}
}

func saveValidatedFile(rootDir string, header *multipart.FileHeader) error {
	filename := filepath.Base(header.Filename)
	if filename == "." || filename == string(filepath.Separator) || filename == "" {
		return errors.New("invalid file name")
	}

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

	destination := filepath.Join(rootDir, filename)
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

	destination := filepath.Join(rootDir, target.fileName)
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
		if entry.Type == "file" && (strings.EqualFold(entry.Name, "SKILL.md") || strings.EqualFold(entry.Name, "skill.md")) {
			selected = append(selected, entry)
		}
	}
	for _, entry := range entries {
		if entry.Type != "file" || !isAllowedSupportingSkillFile(entry.Name) {
			continue
		}
		if strings.EqualFold(entry.Name, "SKILL.md") || strings.EqualFold(entry.Name, "skill.md") {
			continue
		}
		selected = append(selected, entry)
	}
	if len(selected) == 0 {
		return errors.New("GitHub directory must contain SKILL.md or skill.md")
	}
	for _, entry := range selected {
		fileURL := fmt.Sprintf("https://github.com/%s/%s/blob/%s/%s/%s", target.owner, target.repo, target.ref, target.path, entry.Name)
		if err := fetchGitHubFile(ctx, fileURL, rootDir); err != nil {
			return err
		}
	}
	return nil
}

func fetchGitHubDirectoryEntries(ctx context.Context, target gitHubTarget) ([]gitHubDirectoryEntry, error) {
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
		entries = append(entries, gitHubDirectoryEntry{Name: name, Type: entryType})
	}
	if len(entries) == 0 {
		return nil, errors.New("failed to discover files from GitHub directory page")
	}
	return entries, nil
}

func isAllowedSupportingSkillFile(name string) bool {
	ext := strings.ToLower(filepath.Ext(name))
	switch ext {
	case ".md", ".txt", ".json", ".py", ".sh", ".js", ".pdf", ".png", ".jpg", ".jpeg":
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
	if !allowedMimeTypes[mimeType] {
		return fmt.Errorf("%s has unsupported MIME type %s", filename, mimeType)
	}

	ext := strings.ToLower(filepath.Ext(filename))
	if ext == ".exe" {
		return fmt.Errorf("%s is not allowed", filename)
	}
	if ext == ".sh" && mimeType != "text/x-shellscript" && mimeType != "text/plain" {
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
	line = strings.TrimSpace(line)
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
		sentry.CaptureException(err)
		return nil
	}
	return lines
}

func (app *application) collectProgress(jobID string, reader io.Reader) []string {
	scanner := bufio.NewScanner(reader)
	buffer := make([]byte, 0, 64*1024)
	scanner.Buffer(buffer, 1024*1024)
	lines := []string{}
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
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
		_ = app.appendProgress(context.Background(), jobID, "Failed to read evaluator progress: "+err.Error())
	}
	return lines
}
