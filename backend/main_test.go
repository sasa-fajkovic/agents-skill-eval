package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestValidateFilenameSafety(t *testing.T) {
	tests := []struct {
		name     string
		filename string
		wantErr  string
	}{
		{name: "valid", filename: "SKILL.md"},
		{name: "path traversal", filename: "../secret.txt", wantErr: "not allowed"},
		{name: "env file", filename: ".env", wantErr: "environment files are not allowed"},
		{name: "private key", filename: "id_rsa", wantErr: "private key files are not allowed"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validateFilenameSafety(tt.filename)
			if tt.wantErr == "" && err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if tt.wantErr != "" {
				if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
					t.Fatalf("expected error containing %q, got %v", tt.wantErr, err)
				}
			}
		})
	}
}

func TestScanUploadedFile(t *testing.T) {
	dir := t.TempDir()

	write := func(name, content string) string {
		path := filepath.Join(dir, name)
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatalf("write %s: %v", name, err)
		}
		return path
	}

	t.Run("allows skill file with secret words in prose", func(t *testing.T) {
		path := write("SKILL.md", "Do not expose secrets or API keys.")
		if err := scanUploadedFile(path); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})

	t.Run("allows token cost prose in skill file", func(t *testing.T) {
		path := write("SKILL.md", "Always list scripts with their output format. Report current token cost per API call.")
		if err := scanUploadedFile(path); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})

	t.Run("blocks embedded private keys", func(t *testing.T) {
		path := write("notes.md", "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----")
		err := scanUploadedFile(path)
		if err == nil || !strings.Contains(err.Error(), "private key material") {
			t.Fatalf("expected private key rejection, got %v", err)
		}
	})

	t.Run("allows supporting code to be analyzed without execution", func(t *testing.T) {
		path := write("helper.py", "import os\nprint(os.getenv('ANTHROPIC_API_KEY'))\n")
		if err := scanUploadedFile(path); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})

	t.Run("allows normal supporting scripts to be evaluated as data", func(t *testing.T) {
		path := write("helper.sh", "#!/bin/sh\ncurl https://example.com\n")
		if err := scanUploadedFile(path); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})

	t.Run("allows supporting scripts referencing their own env vars", func(t *testing.T) {
		path := write("helper.py", "import os\nprint(os.getenv('ATLASSIAN_API_TOKEN'))\n")
		if err := scanUploadedFile(path); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})

	t.Run("blocks active pdf content", func(t *testing.T) {
		path := write("doc.pdf", "%PDF-1.4\n/OpenAction << /S /JavaScript >>")
		err := scanUploadedFile(path)
		if err == nil || !strings.Contains(err.Error(), "active PDF content") {
			t.Fatalf("expected pdf error, got %v", err)
		}
	})

	t.Run("ignores binary files outside scan list", func(t *testing.T) {
		path := write("image.bin", "curl https://example.com")
		if err := scanUploadedFile(path); err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})
}

func TestScanUploadedPackage(t *testing.T) {
	t.Run("allows nested skill directory", func(t *testing.T) {
		dir := t.TempDir()
		nested := filepath.Join(dir, "pr-review", "scripts")
		if err := os.MkdirAll(nested, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "pr-review", "SKILL.md"), []byte("# skill"), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(nested, "check.sh"), []byte("echo ok"), 0o644); err != nil {
			t.Fatal(err)
		}
		err := scanUploadedPackage(dir)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
	})

	t.Run("rejects blocked file within nested package", func(t *testing.T) {
		dir := t.TempDir()
		nested := filepath.Join(dir, "pr-review")
		if err := os.MkdirAll(nested, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(nested, ".env"), []byte("SECRET=1"), 0o644); err != nil {
			t.Fatal(err)
		}
		err := scanUploadedPackage(dir)
		if err == nil || !strings.Contains(err.Error(), "environment files are not allowed") {
			t.Fatalf("expected filename rejection, got %v", err)
		}
	})
}

func TestSanitizeRelativeUploadPath(t *testing.T) {
	tests := []struct {
		name    string
		input   string
		want    string
		wantErr string
	}{
		{name: "flat file", input: "SKILL.md", want: "SKILL.md"},
		{name: "nested path", input: "pr-review/scripts/check.sh", want: "pr-review/scripts/check.sh"},
		{name: "windows path", input: `pr-review\SKILL.md`, want: "pr-review/SKILL.md"},
		{name: "path traversal", input: "../SKILL.md", wantErr: "not allowed"},
		{name: "empty", input: "   ", wantErr: "invalid file name"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := sanitizeRelativeUploadPath(tt.input)
			if tt.wantErr == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				if got != tt.want {
					t.Fatalf("unexpected path: got %q want %q", got, tt.want)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), tt.wantErr) {
				t.Fatalf("expected error containing %q, got %v", tt.wantErr, err)
			}
		})
	}
}

func TestRedactSecrets(t *testing.T) {
	input := strings.Join([]string{
		"Bearer super-secret-token",
		"ANTHROPIC_API_KEY=abc123",
		"ghp_123456789012345678901234567890123456",
		"-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----",
	}, "\n")

	got := redactSecrets(input)
	for _, forbidden := range []string{"super-secret-token", "abc123", "ghp_123456789012345678901234567890123456", "BEGIN OPENSSH PRIVATE KEY"} {
		if strings.Contains(got, forbidden) {
			t.Fatalf("expected %q to be redacted from %q", forbidden, got)
		}
	}
	for _, expected := range []string{"Bearer [REDACTED]", "ANTHROPIC_API_KEY=[REDACTED]", "[REDACTED_TOKEN]", "[REDACTED_PRIVATE_KEY]"} {
		if !strings.Contains(got, expected) {
			t.Fatalf("expected %q in %q", expected, got)
		}
	}
}

func TestDeterministicIssues(t *testing.T) {
	got := deterministicIssues([]any{" one ", "", 2, nil})
	want := []string{"one", "2", "<nil>"}
	if len(got) != len(want) {
		t.Fatalf("unexpected length: got %v want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("unexpected value at %d: got %q want %q", i, got[i], want[i])
		}
	}
}

func TestComputeOverallScore(t *testing.T) {
	if got := computeOverallScore(map[string]any{"issues": []any{"a", "b"}}, nil); got != 80 {
		t.Fatalf("unexpected score: %d", got)
	}
	if got := computeOverallScore(map[string]any{"issues": []any{"a", "b", "c", "d", "e", "f", "g"}}, nil); got != 40 {
		t.Fatalf("unexpected clamped deterministic score: %d", got)
	}
	analysis := &llmAnalysis{QualityTier: "excellent"}
	if got := computeOverallScore(map[string]any{"issues": []any{"a", "b"}}, analysis); got != 85 {
		t.Fatalf("unexpected score with llm analysis: %d", got)
	}
}

func TestSummarizeIssues(t *testing.T) {
	if got := summarizeIssues(map[string]any{"issues": []any{"first issue", "second"}}, nil); !strings.Contains(got, "Primary issue: first issue") {
		t.Fatalf("unexpected summary: %q", got)
	}
	if got := summarizeIssues(map[string]any{}, nil); !strings.Contains(got, "no deterministic issues") {
		t.Fatalf("unexpected summary: %q", got)
	}
	if got := summarizeIssues(map[string]any{}, &llmAnalysis{Strengths: []string{"Clear structure"}}); !strings.Contains(got, "Key strength: Clear structure") {
		t.Fatalf("unexpected llm summary: %q", got)
	}
}

func TestParseIntDefault(t *testing.T) {
	if got := parseIntDefault("42", 7); got != 42 {
		t.Fatalf("expected 42, got %d", got)
	}
	if got := parseIntDefault("0", 7); got != 7 {
		t.Fatalf("expected fallback, got %d", got)
	}
	if got := parseIntDefault("bad", 7); got != 7 {
		t.Fatalf("expected fallback, got %d", got)
	}
}

func TestResolveGitHubTarget(t *testing.T) {
	t.Run("blob url", func(t *testing.T) {
		target, err := resolveGitHubTarget("https://github.com/org/repo/blob/main/path/SKILL.md")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if target.kind != "file" || target.fileName != "SKILL.md" || !strings.Contains(target.url, "raw.githubusercontent.com") {
			t.Fatalf("unexpected target: %#v", target)
		}
	})

	t.Run("tree url", func(t *testing.T) {
		target, err := resolveGitHubTarget("https://github.com/org/repo/tree/main/skills/example")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if target.kind != "directory" || target.path != "skills/example" {
			t.Fatalf("unexpected target: %#v", target)
		}
	})

	t.Run("raw url", func(t *testing.T) {
		target, err := resolveGitHubTarget("https://raw.githubusercontent.com/org/repo/main/SKILL.md")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if target.kind != "file" || target.fileName != "SKILL.md" {
			t.Fatalf("unexpected target: %#v", target)
		}
	})

	t.Run("rejects non github host", func(t *testing.T) {
		_, err := resolveGitHubTarget("https://example.com/org/repo/blob/main/SKILL.md")
		if err == nil || !strings.Contains(err.Error(), "only github.com") {
			t.Fatalf("expected host error, got %v", err)
		}
	})
}

func TestSplitGitHubBlobPath(t *testing.T) {
	ref, path, err := splitGitHubBlobPath([]string{"org", "repo", "blob", "main", "dir", "SKILL.md"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if ref != "main" || path != "dir/SKILL.md" {
		t.Fatalf("unexpected split: %q %q", ref, path)
	}
	if _, _, err := splitGitHubBlobPath([]string{"org", "repo", "blob", "main"}); err == nil {
		t.Fatal("expected error for short blob path")
	}
}

func TestAllowedHelpers(t *testing.T) {
	if !isAllowedSupportingSkillFile("notes.md") || isAllowedSupportingSkillFile("archive.zip") {
		t.Fatal("unexpected supporting file allowlist result")
	}
	if !isAllowedSupportingSkillFile("assets/viewer.html") || !isAllowedSupportingSkillFile("scripts/templates/comments.xml") || !isAllowedSupportingSkillFile("schemas/doc.xsd") {
		t.Fatal("expected recursive public skill file types to be allowed")
	}
	if !isAllowedGitHubHost("GitHub.com") || isAllowedGitHubHost("evilgithub.com") {
		t.Fatal("unexpected github host allowlist result")
	}
}

func TestFetchGitHubDirectoryEntriesRecursive(t *testing.T) {
	oldTransport := http.DefaultTransport
	defer func() { http.DefaultTransport = oldTransport }()

	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/repos/test/skills/git/trees/main" || r.URL.RawQuery != "recursive=1" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"tree": []map[string]string{
				{"path": "skills/mcp-builder/SKILL.md", "type": "blob"},
				{"path": "skills/mcp-builder/LICENSE.txt", "type": "blob"},
				{"path": "skills/mcp-builder/reference", "type": "tree"},
				{"path": "skills/mcp-builder/reference/one.md", "type": "blob"},
				{"path": "skills/mcp-builder/reference/two.md", "type": "blob"},
				{"path": "skills/mcp-builder/scripts", "type": "tree"},
				{"path": "skills/mcp-builder/scripts/a.py", "type": "blob"},
				{"path": "skills/mcp-builder/scripts/b.py", "type": "blob"},
				{"path": "skills/other-skill/SKILL.md", "type": "blob"},
			},
		})
	}))
	defer server.Close()

	transport := server.Client().Transport
	http.DefaultTransport = roundTripFunc(func(req *http.Request) (*http.Response, error) {
		if req.URL.Host == "api.github.com" {
			req.URL.Scheme = "https"
			req.URL.Host = strings.TrimPrefix(server.URL, "https://")
		}
		return transport.RoundTrip(req)
	})

	entries, err := fetchGitHubDirectoryEntries(context.Background(), gitHubTarget{owner: "test", repo: "skills", ref: "main", path: "skills/mcp-builder"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(entries) != 8 {
		t.Fatalf("unexpected recursive entry count: %d", len(entries))
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func TestValidateFileType(t *testing.T) {
	if err := validateFileType("script.sh", "text/plain"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if err := validateFileType("SKILL.md", "text/plain; charset=utf-8"); err != nil {
		t.Fatalf("expected charset text/plain to be allowed, got %v", err)
	}
	if err := validateFileType("script.sh", "text/x-sh"); err != nil {
		t.Fatalf("expected text/x-sh shell upload to be allowed, got %v", err)
	}
	if err := validateFileType("copilot-review/SKILL.md", "text/html"); err != nil {
		t.Fatalf("expected text/html markdown upload to be allowed, got %v", err)
	}
	if err := validateFileType("templates/comments.xml", "application/xml"); err != nil {
		t.Fatalf("expected application/xml upload to be allowed, got %v", err)
	}
	if err := validateFileType("schemas/doc.xsd", "text/xml"); err != nil {
		t.Fatalf("expected text/xml upload to be allowed, got %v", err)
	}
	if err := validateFileType("bad.jar", "application/java-archive"); err == nil {
		t.Fatal("expected jar rejection")
	}
	if err := validateFileType("bad.ps1", "text/plain"); err == nil {
		t.Fatal("expected blocked extension rejection")
	}
	if err := validateFileType("bad.bin", "application/x-mach-binary"); err == nil {
		t.Fatal("expected executable mime rejection")
	}
	if err := validateFileType("bad.exe", "application/octet-stream"); err == nil {
		t.Fatal("expected exe rejection")
	}
	if err := validateFileType("script.sh", "image/png"); err == nil {
		t.Fatal("expected shell mime rejection")
	}
}

func TestFinalizeEvaluation(t *testing.T) {
	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond})}
	raw := `{"status":"ok","skill_name":"demo","skill_content":"skill body","supporting_context":"extra context","deterministic":{"issues":["missing tests"]}}`

	result, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1"}, raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result, `"overall_score":90`) || !strings.Contains(result, `"skill_name":"demo"`) {
		t.Fatalf("unexpected final result: %s", result)
	}
}

func TestFinalizeEvaluationWithOptionalLLM(t *testing.T) {
	old := os.Getenv("ANTHROPIC_API_KEY")
	oldBaseURL := os.Getenv("ANTHROPIC_BASE_URL")
	defer func() {
		if old == "" {
			_ = os.Unsetenv("ANTHROPIC_API_KEY")
		} else {
			_ = os.Setenv("ANTHROPIC_API_KEY", old)
		}
		if oldBaseURL == "" {
			_ = os.Unsetenv("ANTHROPIC_BASE_URL")
		} else {
			_ = os.Setenv("ANTHROPIC_BASE_URL", oldBaseURL)
		}
	}()
	_ = os.Setenv("ANTHROPIC_API_KEY", "configured")

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/messages" {
			http.NotFound(w, r)
			return
		}
		if got := r.Header.Get("x-api-key"); got != "configured" {
			t.Fatalf("unexpected api key: %q", got)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"content":[{"type":"text","text":"{\"strengths\":[\"Clear scope\"],\"weaknesses\":[\"Missing examples\"],\"suggestions\":[\"Add one end-to-end example\"],\"security_flags\":[\"No explicit failure fallback\"],\"quality_tier\":\"good\"}"}]}`)
	}))
	defer server.Close()
	_ = os.Setenv("ANTHROPIC_BASE_URL", server.URL+"/v1/messages")

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), httpClient: server.Client()}
	raw := `{"status":"ok","skill_name":"demo","skill_content":"skill body","supporting_context":"extra context","deterministic":{"issues":[]}}`

	result, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1", EnableLLM: true, LLMRequested: true}, raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result, `"llm_enabled":true`) || !strings.Contains(result, `"llm_analysis"`) || !strings.Contains(result, `"provider":"anthropic"`) {
		t.Fatalf("expected optional llm payload, got %s", result)
	}
}

func TestFinalizeEvaluationWithOptionalLLMFallsBackOnProviderError(t *testing.T) {
	old := os.Getenv("ANTHROPIC_API_KEY")
	oldBaseURL := os.Getenv("ANTHROPIC_BASE_URL")
	defer func() {
		if old == "" {
			_ = os.Unsetenv("ANTHROPIC_API_KEY")
		} else {
			_ = os.Setenv("ANTHROPIC_API_KEY", old)
		}
		if oldBaseURL == "" {
			_ = os.Unsetenv("ANTHROPIC_BASE_URL")
		} else {
			_ = os.Setenv("ANTHROPIC_BASE_URL", oldBaseURL)
		}
	}()
	_ = os.Setenv("ANTHROPIC_API_KEY", "configured")

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = io.WriteString(w, `{"error":{"message":"provider down"}}`)
	}))
	defer server.Close()
	_ = os.Setenv("ANTHROPIC_BASE_URL", server.URL)

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), httpClient: server.Client()}
	raw := `{"status":"ok","skill_name":"demo","skill_content":"skill body","supporting_context":"extra context","deterministic":{"issues":[]}}`

	result, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1", EnableLLM: true, LLMRequested: true}, raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strings.Contains(result, `"llm_analysis"`) {
		t.Fatalf("expected deterministic fallback, got %s", result)
	}
}

func TestParseAnthropicAnalysis(t *testing.T) {
	body := []byte("{\"content\":[{\"type\":\"text\",\"text\":\"```json\\n{\\\"strengths\\\":[\\\"Clear\\\"],\\\"weaknesses\\\":[],\\\"suggestions\\\":[],\\\"security_flags\\\":[],\\\"quality_tier\\\":\\\"excellent\\\"}\\n```\"}]}")
	analysis, err := parseAnthropicAnalysis(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if analysis.QualityTier != "excellent" || len(analysis.Strengths) != 1 {
		t.Fatalf("unexpected analysis: %+v", analysis)
	}
}

func TestParseAnthropicError(t *testing.T) {
	msg := parseAnthropicError([]byte(`{"error":{"message":"ANTHROPIC_API_KEY=secret"}}`), "bad gateway")
	if strings.Contains(msg, "secret") {
		t.Fatalf("expected secret to be redacted: %q", msg)
	}
	if !strings.Contains(msg, "anthropic request failed") {
		t.Fatalf("unexpected error message: %q", msg)
	}
}

func TestBuildAnthropicPrompt(t *testing.T) {
	prompt := buildAnthropicPrompt(evalContainerResult{
		SkillContent:      strings.Repeat("a", 20),
		SupportingContext: "context",
		Deterministic:     map[string]any{"issues": []any{"missing examples"}},
	})
	for _, expected := range []string{"Deterministic findings", "Primary SKILL.md content", "Supporting context"} {
		if !strings.Contains(prompt, expected) {
			t.Fatalf("expected %q in prompt: %s", expected, prompt)
		}
	}
}

func TestNormalizeQualityTier(t *testing.T) {
	if got := normalizeQualityTier("GOOD"); got != "good" {
		t.Fatalf("unexpected quality tier: %q", got)
	}
	if got := normalizeQualityTier("unknown"); got != "needs_work" {
		t.Fatalf("unexpected fallback quality tier: %q", got)
	}
}

func TestFinalizeEvaluationErrors(t *testing.T) {
	app := &application{}
	if _, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1"}, `not-json`); err == nil {
		t.Fatal("expected parse error")
	}
	if _, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1"}, `{"status":"error","message":"ANTHROPIC_API_KEY=secret"}`); err == nil || strings.Contains(err.Error(), "secret") {
		t.Fatalf("expected redacted evaluator error, got %v", err)
	}
}

func TestParseBool(t *testing.T) {
	for _, value := range []string{"1", "true", "yes", "on", " TRUE "} {
		if !parseBool(value) {
			t.Fatalf("expected %q to parse as true", value)
		}
	}
	for _, value := range []string{"", "0", "false", "no", "off"} {
		if parseBool(value) {
			t.Fatalf("expected %q to parse as false", value)
		}
	}
}

func TestShouldEmitSentryTestEvent(t *testing.T) {
	old := os.Getenv("SENTRY_TEST_EVENT")
	defer func() {
		if old == "" {
			_ = os.Unsetenv("SENTRY_TEST_EVENT")
		} else {
			_ = os.Setenv("SENTRY_TEST_EVENT", old)
		}
	}()

	_ = os.Setenv("SENTRY_TEST_EVENT", "true")
	if !shouldEmitSentryTestEvent() {
		t.Fatal("expected sentry test event flag to be enabled")
	}
	_ = os.Setenv("SENTRY_TEST_EVENT", "false")
	if shouldEmitSentryTestEvent() {
		t.Fatal("expected sentry test event flag to be disabled")
	}
}

func TestHandleUploadRejectsOversizePayload(t *testing.T) {
	body := bytes.Repeat([]byte("a"), maxUploadSize+1)
	req := httptest.NewRequest(http.MethodPost, "/upload", bytes.NewReader(body))
	req.Header.Set("Content-Type", "multipart/form-data; boundary=test")
	rr := httptest.NewRecorder()

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond})}
	app.handleUpload(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "payload exceeds 5MB") {
		t.Fatalf("unexpected body: %s", rr.Body.String())
	}
}

func TestHandleUploadRejectsExecutableAttachment(t *testing.T) {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("files", "bad.exe")
	if err != nil {
		t.Fatalf("create form file: %v", err)
	}
	if _, err := part.Write([]byte("MZ executable")); err != nil {
		t.Fatalf("write form file: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("close writer: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/upload", &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	rr := httptest.NewRecorder()

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond})}
	app.handleUpload(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "not an allowed upload type") {
		t.Fatalf("unexpected body: %s", rr.Body.String())
	}
}
