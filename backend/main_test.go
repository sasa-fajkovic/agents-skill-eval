package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime/multipart"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	miniredis "github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"
)

func testConfig() appConfig {
	return appConfig{
		Scoring: scoringConfig{ErrorPenalty: 5, ErrorCap: 70, WarningPenalty: 2, WarningCap: 24},
		Providers: map[string]providerConfig{
			"anthropic": {Model: "claude-haiku-4-5", MaxTokens: 1200, BaseURL: "https://api.anthropic.com/v1/messages"},
			"openai":    {Model: "gpt-4.1-nano", MaxTokens: 1200, BaseURL: "https://api.openai.com/v1/chat/completions"},
			"groq":      {Model: "llama-3.3-70b-versatile", MaxTokens: 1200, BaseURL: "https://api.groq.com/openai/v1/chat/completions"},
			"gemini":    {Model: "gemini-2.0-flash", MaxTokens: 1200, BaseURL: "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"},
		},
		Pricing: map[string]pricingEntry{
			"claude-haiku-4": {InputPerM: 0.80, OutputPerM: 4.0},
			"gpt-4.1-nano":  {InputPerM: 0.10, OutputPerM: 0.40},
			"llama-3.3-70b": {InputPerM: 0, OutputPerM: 0},
		},
	}
}

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

func TestClientIPTrustsOnlyLoopbackProxy(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.RemoteAddr = "203.0.113.8:4321"
	req.Header.Set("X-Forwarded-For", "198.51.100.4")
	if got := clientIP(req); got != "203.0.113.8" {
		t.Fatalf("expected remote addr IP, got %q", got)
	}

	trusted := httptest.NewRequest(http.MethodGet, "/", nil)
	trusted.RemoteAddr = "127.0.0.1:9000"
	trusted.Header.Set("X-Forwarded-For", "198.51.100.4, 127.0.0.1")
	if got := clientIP(trusted); got != "198.51.100.4" {
		t.Fatalf("expected forwarded IP, got %q", got)
	}
}

func TestHashEvaluationRequestStableForSameFiles(t *testing.T) {
	dirA := t.TempDir()
	dirB := t.TempDir()
	files := map[string]string{
		"SKILL.md":            "# Demo\ntrigger",
		"scripts/helper.sh":   "echo ok",
		"references/readme.md": "context",
	}
	for name, content := range files {
		pathA := filepath.Join(dirA, filepath.FromSlash(name))
		pathB := filepath.Join(dirB, filepath.FromSlash(name))
		if err := os.MkdirAll(filepath.Dir(pathA), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(filepath.Dir(pathB), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(pathA, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(pathB, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	hashA, err := hashEvaluationRequest(dirA, true, "openai")
	if err != nil {
		t.Fatalf("hash A: %v", err)
	}
	hashB, err := hashEvaluationRequest(dirB, true, "openai")
	if err != nil {
		t.Fatalf("hash B: %v", err)
	}
	if hashA != hashB {
		t.Fatalf("expected stable hash, got %q vs %q", hashA, hashB)
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

func TestComputeOverallTier(t *testing.T) {
	if got := computeOverallTier(map[string]any{"issues": []any{}}, nil); got != "excellent" {
		t.Fatalf("unexpected deterministic tier: %q", got)
	}
	if got := computeOverallTier(map[string]any{"issues": []any{"a", "b", "c"}}, nil); got != "needs_work" {
		t.Fatalf("unexpected deterministic tier: %q", got)
	}
	if got := computeOverallTier(map[string]any{"issues": []any{}}, &llmAnalysis{QualityTier: "poor"}); got != "poor" {
		t.Fatalf("unexpected llm tier: %q", got)
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
		if target.kind != "directory" || target.path != "skills/example" || target.ref != "main" {
			t.Fatalf("unexpected target: %#v", target)
		}
	})

	t.Run("raw url", func(t *testing.T) {
		target, err := resolveGitHubTarget("https://raw.githubusercontent.com/org/repo/main/SKILL.md")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if target.kind != "file" || target.fileName != "SKILL.md" || target.path != "SKILL.md" {
			t.Fatalf("unexpected target: %#v", target)
		}
	})

	t.Run("blob url with slash ref keeps ambiguous path for later resolution", func(t *testing.T) {
		target, err := resolveGitHubTarget("https://github.com/org/repo/blob/feature/test/path/SKILL.md")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if target.kind != "file" || target.ref != "feature" || target.path != "test/path/SKILL.md" || target.refPath != "feature/test/path/SKILL.md" {
			t.Fatalf("unexpected target: %#v", target)
		}
	})

	t.Run("tree url with slash ref keeps ambiguous path for later resolution", func(t *testing.T) {
		target, err := resolveGitHubTarget("https://github.com/org/repo/tree/release/2026/skills/demo")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if target.kind != "directory" || target.ref != "release" || target.path != "2026/skills/demo" || target.refPath != "release/2026/skills/demo" {
			t.Fatalf("unexpected target: %#v", target)
		}
	})

	t.Run("raw url with slash ref keeps ambiguous path for later resolution", func(t *testing.T) {
		target, err := resolveGitHubTarget("https://raw.githubusercontent.com/org/repo/release/2026/SKILL.md")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if target.kind != "file" || target.ref != "release" || target.path != "2026/SKILL.md" || target.refPath != "release/2026/SKILL.md" {
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

func TestFetchGitHubDirectoryEntriesRejectsTruncatedTree(t *testing.T) {
	oldTransport := http.DefaultTransport
	defer func() { http.DefaultTransport = oldTransport }()

	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"tree":      []map[string]string{{"path": "skills/demo/SKILL.md", "type": "blob"}},
			"truncated": true,
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

	_, err := fetchGitHubDirectoryEntries(context.Background(), gitHubTarget{owner: "test", repo: "skills", ref: "main", path: "skills/demo"})
	if err == nil || !strings.Contains(err.Error(), "too large") {
		t.Fatalf("expected truncated tree error, got %v", err)
	}
	if statusCodeForError(err, 0) != http.StatusBadRequest {
		t.Fatalf("expected bad request status, got %d", statusCodeForError(err, 0))
	}
}

func TestStatusCodeForError(t *testing.T) {
	if got := statusCodeForError(statusError{status: http.StatusServiceUnavailable, message: "busy"}, http.StatusBadRequest); got != http.StatusServiceUnavailable {
		t.Fatalf("unexpected status code: %d", got)
	}
	if got := statusCodeForError(errors.New("plain"), http.StatusBadRequest); got != http.StatusBadRequest {
		t.Fatalf("unexpected fallback status code: %d", got)
	}
}

func TestResolveGitHubTargetRefWithSlashBranchFile(t *testing.T) {
	oldTransport := http.DefaultTransport
	defer func() { http.DefaultTransport = oldTransport }()

	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/repos/org/repo/contents/path/SKILL.md" || r.URL.Query().Get("ref") != "feature/test" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"type": "file"})
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

	target, err := resolveGitHubTarget("https://github.com/org/repo/blob/feature/test/path/SKILL.md")
	if err != nil {
		t.Fatalf("unexpected parse error: %v", err)
	}
	resolved, err := resolveGitHubTargetRef(context.Background(), target)
	if err != nil {
		t.Fatalf("unexpected resolution error: %v", err)
	}
	if resolved.ref != "feature/test" || resolved.path != "path/SKILL.md" {
		t.Fatalf("unexpected resolved target: %#v", resolved)
	}
	if !strings.Contains(resolved.url, "/feature/test/path/SKILL.md") {
		t.Fatalf("unexpected resolved raw url: %s", resolved.url)
	}
}

func TestResolveGitHubTargetRefWithSlashBranchDirectory(t *testing.T) {
	oldTransport := http.DefaultTransport
	defer func() { http.DefaultTransport = oldTransport }()

	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/repos/org/repo/contents/skills/demo" || r.URL.Query().Get("ref") != "release/2026" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode([]map[string]any{{"type": "file", "name": "SKILL.md"}})
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

	target, err := resolveGitHubTarget("https://github.com/org/repo/tree/release/2026/skills/demo")
	if err != nil {
		t.Fatalf("unexpected parse error: %v", err)
	}
	resolved, err := resolveGitHubTargetRef(context.Background(), target)
	if err != nil {
		t.Fatalf("unexpected resolution error: %v", err)
	}
	if resolved.ref != "release/2026" || resolved.path != "skills/demo" {
		t.Fatalf("unexpected resolved target: %#v", resolved)
	}
	if !strings.Contains(resolved.htmlURL, "/tree/release/2026/skills/demo") {
		t.Fatalf("unexpected resolved html url: %s", resolved.htmlURL)
	}
}

func TestResolveGitHubTargetRefWithSlashBranchRawFile(t *testing.T) {
	oldTransport := http.DefaultTransport
	defer func() { http.DefaultTransport = oldTransport }()

	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/repos/org/repo/contents/SKILL.md" || r.URL.Query().Get("ref") != "release/2026" {
			http.NotFound(w, r)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"type": "file"})
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

	target, err := resolveGitHubTarget("https://raw.githubusercontent.com/org/repo/release/2026/SKILL.md")
	if err != nil {
		t.Fatalf("unexpected parse error: %v", err)
	}
	resolved, err := resolveGitHubTargetRef(context.Background(), target)
	if err != nil {
		t.Fatalf("unexpected resolution error: %v", err)
	}
	if resolved.ref != "release/2026" || resolved.path != "SKILL.md" {
		t.Fatalf("unexpected resolved target: %#v", resolved)
	}
}

func TestGitHubHTTPErrorStatusMapping(t *testing.T) {
	tests := []struct {
		name       string
		statusCode int
		status     string
		wantCode   int
		wantText   string
	}{
		{name: "not found", statusCode: http.StatusNotFound, status: "404 Not Found", wantCode: http.StatusBadRequest, wantText: "could not be found"},
		{name: "forbidden", statusCode: http.StatusForbidden, status: "403 Forbidden", wantCode: http.StatusServiceUnavailable, wantText: "temporarily unavailable"},
		{name: "server error", statusCode: http.StatusBadGateway, status: "502 Bad Gateway", wantCode: http.StatusBadGateway, wantText: "GitHub returned 502 Bad Gateway"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := gitHubHTTPError(tt.statusCode, tt.status)
			if got := statusCodeForError(err, 0); got != tt.wantCode {
				t.Fatalf("unexpected status code: got %d want %d", got, tt.wantCode)
			}
			if !strings.Contains(err.Error(), tt.wantText) {
				t.Fatalf("unexpected message: %v", err)
			}
		})
	}
}

func TestGitHubUpstreamErrorTimeout(t *testing.T) {
	err := gitHubUpstreamError(timeoutErr{}, "failed to fetch GitHub URL")
	if got := statusCodeForError(err, 0); got != http.StatusBadGateway {
		t.Fatalf("unexpected status code: %d", got)
	}
	if !strings.Contains(err.Error(), "timed out") {
		t.Fatalf("unexpected error message: %v", err)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

type timeoutErr struct{}

func (timeoutErr) Error() string   { return "timeout" }
func (timeoutErr) Timeout() bool   { return true }
func (timeoutErr) Temporary() bool { return true }

var _ net.Error = timeoutErr{}

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
	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), cfg: testConfig()}
	raw := `{"status":"ok","skill_name":"demo","skill_description":"Use when validating skill packages.\n\nKeeps output portable.","skill_compatibility":"Claude Code\nCodex CLI","skill_content":"skill body","supporting_context":"extra context","overall_score":90,"overall_tier":"excellent","summary":"demo is mostly portable.","deterministic":{"issues":[{"rule_id":"missing_tests","severity":"warning","message":"missing tests","reason":"scripts need tests"}]}}`

	result, _, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1"}, raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result, `"overall_score":90`) || !strings.Contains(result, `"overall_tier":"excellent"`) || !strings.Contains(result, `"skill_name":"demo"`) || !strings.Contains(result, `"skill_description":"Use when validating skill packages.\n\nKeeps output portable."`) || !strings.Contains(result, `"skill_compatibility":"Claude Code\nCodex CLI"`) {
		t.Fatalf("unexpected final result: %s", result)
	}
}

func TestFinalizeEvaluationWithOptionalLLM(t *testing.T) {
	old := os.Getenv("ANTHROPIC_API_KEY")
	oldProvider := os.Getenv("LLM_PROVIDER")
	defer func() {
		if old == "" {
			_ = os.Unsetenv("ANTHROPIC_API_KEY")
		} else {
			_ = os.Setenv("ANTHROPIC_API_KEY", old)
		}
		if oldProvider == "" {
			_ = os.Unsetenv("LLM_PROVIDER")
		} else {
			_ = os.Setenv("LLM_PROVIDER", oldProvider)
		}
	}()
	_ = os.Setenv("ANTHROPIC_API_KEY", "configured")
	_ = os.Setenv("LLM_PROVIDER", "anthropic")

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

	cfg := testConfig()
	cfg.Providers["anthropic"] = providerConfig{Model: "claude-haiku-4-5", MaxTokens: 1200, BaseURL: server.URL + "/v1/messages"}
	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), httpClient: server.Client(), cfg: cfg}
	raw := `{"status":"ok","skill_name":"demo","skill_content":"skill body","supporting_context":"extra context","overall_score":100,"overall_tier":"excellent","summary":"demo passes deterministic evaluation.","deterministic":{"issues":[]}}`

	result, _, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1", EnableLLM: true, LLMRequested: true, LLMProvider: "anthropic"}, raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result, `"llm_enabled":true`) || !strings.Contains(result, `"llm_analysis"`) || !strings.Contains(result, `"mode":"opt_in"`) || !strings.Contains(result, `"overall_tier":"good"`) || !strings.Contains(result, `"provider":"anthropic"`) || !strings.Contains(result, `"model":"claude-haiku-4-5"`) {
		t.Fatalf("expected optional llm payload, got %s", result)
	}
}

func TestHandleResultConsumeReleasesState(t *testing.T) {
	ctx := context.Background()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	app := &application{rdb: rdb, cfg: testConfig()}
	jobID := "job-consume"
	result := `{"status":"ok","skill_name":"demo","overall_score":92,"overall_tier":"excellent"}`
	if err := rdb.Set(ctx, redisResultKey(jobID), result, time.Minute).Err(); err != nil {
		t.Fatalf("set result: %v", err)
	}
	if err := rdb.RPush(ctx, redisProgressKey(jobID), "Evaluation completed.").Err(); err != nil {
		t.Fatalf("set progress: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/result/"+jobID+"?consume=true", nil)
	rec := httptest.NewRecorder()
	app.handleResult(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("unexpected status: %d body=%s", rec.Code, rec.Body.String())
	}
	if got, err := rdb.Get(ctx, redisResultKey(jobID)).Result(); err != redis.Nil || got != "" {
		t.Fatalf("expected result to be released, got value=%q err=%v", got, err)
	}
	if got, err := rdb.LRange(ctx, redisProgressKey(jobID), 0, -1).Result(); err != nil || len(got) != 0 {
		t.Fatalf("expected progress to be released, got value=%v err=%v", got, err)
	}
}

func TestFinalizeEvaluationWithOptionalLLMFallsBackOnProviderError(t *testing.T) {
	old := os.Getenv("ANTHROPIC_API_KEY")
	oldProvider := os.Getenv("LLM_PROVIDER")
	defer func() {
		if old == "" {
			_ = os.Unsetenv("ANTHROPIC_API_KEY")
		} else {
			_ = os.Setenv("ANTHROPIC_API_KEY", old)
		}
		if oldProvider == "" {
			_ = os.Unsetenv("LLM_PROVIDER")
		} else {
			_ = os.Setenv("LLM_PROVIDER", oldProvider)
		}
	}()
	_ = os.Setenv("ANTHROPIC_API_KEY", "configured")
	_ = os.Setenv("LLM_PROVIDER", "anthropic")

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = io.WriteString(w, `{"error":{"message":"provider down"}}`)
	}))
	defer server.Close()

	cfg := testConfig()
	cfg.Providers["anthropic"] = providerConfig{Model: "claude-haiku-4-5", MaxTokens: 1200, BaseURL: server.URL}
	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), httpClient: server.Client(), cfg: cfg}
	raw := `{"status":"ok","skill_name":"demo","skill_content":"skill body","supporting_context":"extra context","deterministic":{"issues":[]}}`

	result, _, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1", EnableLLM: true, LLMRequested: true, LLMProvider: "anthropic"}, raw)
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

func TestBuildLLMPrompt(t *testing.T) {
	prompt := buildLLMPrompt(evalContainerResult{
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

func TestParseOpenAIAnalysis(t *testing.T) {
	body := []byte(`{"choices":[{"message":{"role":"assistant","content":"{\"strengths\":[\"Clear\"],\"weaknesses\":[],\"suggestions\":[],\"security_flags\":[],\"quality_tier\":\"excellent\"}"}}]}`)
	analysis, err := parseOpenAIAnalysis(body)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if analysis.QualityTier != "excellent" || len(analysis.Strengths) != 1 {
		t.Fatalf("unexpected analysis: %+v", analysis)
	}
}

func TestParseOpenAIError(t *testing.T) {
	msg := parseOpenAIError([]byte(`{"error":{"message":"OPENAI_API_KEY=secret"}}`), "bad gateway")
	if strings.Contains(msg, "secret") {
		t.Fatalf("expected secret to be redacted: %q", msg)
	}
	if !strings.Contains(msg, "openai request failed") {
		t.Fatalf("unexpected error message: %q", msg)
	}
}

func TestBuildOpenAIRequestBodyUsesMaxTokensForGPT41(t *testing.T) {
	body, err := buildOpenAIRequestBody("gpt-4.1", 1200, "sys", "user")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	jsonBody := string(encoded)
	if !strings.Contains(jsonBody, `"max_tokens":1200`) {
		t.Fatalf("expected max_tokens in request body: %s", jsonBody)
	}
	if strings.Contains(jsonBody, `"max_completion_tokens"`) {
		t.Fatalf("did not expect max_completion_tokens in request body: %s", jsonBody)
	}
}

func TestBuildOpenAIRequestBodyUsesMaxCompletionTokensForGPT53ChatLatest(t *testing.T) {
	body, err := buildOpenAIRequestBody("gpt-5.3-chat-latest", 1200, "sys", "user")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	jsonBody := string(encoded)
	if !strings.Contains(jsonBody, `"max_completion_tokens":1200`) {
		t.Fatalf("expected max_completion_tokens in request body: %s", jsonBody)
	}
	if strings.Contains(jsonBody, `"max_tokens"`) {
		t.Fatalf("did not expect max_tokens in request body: %s", jsonBody)
	}
}

func TestBuildOpenAIRequestBodyUsesMaxCompletionTokensForGPT54(t *testing.T) {
	body, err := buildOpenAIRequestBody("gpt-5.4", 1200, "sys", "user")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	jsonBody := string(encoded)
	if !strings.Contains(jsonBody, `"max_completion_tokens":1200`) {
		t.Fatalf("expected max_completion_tokens in request body: %s", jsonBody)
	}
	if strings.Contains(jsonBody, `"max_tokens"`) {
		t.Fatalf("did not expect max_tokens in request body: %s", jsonBody)
	}
}

func TestRunOpenAIReviewWithGPT41UsesMaxTokens(t *testing.T) {
	oldKey := os.Getenv("OPENAI_API_KEY")
	defer func() {
		if oldKey == "" {
			_ = os.Unsetenv("OPENAI_API_KEY")
		} else {
			_ = os.Setenv("OPENAI_API_KEY", oldKey)
		}
	}()
	_ = os.Setenv("OPENAI_API_KEY", "configured-openai")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read body: %v", err)
		}
		jsonBody := string(body)
		if !strings.Contains(jsonBody, `"model":"gpt-4.1"`) || !strings.Contains(jsonBody, `"max_tokens":1200`) || strings.Contains(jsonBody, `"max_completion_tokens"`) {
			t.Fatalf("unexpected request body: %s", jsonBody)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"{\"strengths\":[\"Concise\"],\"weaknesses\":[],\"suggestions\":[],\"security_flags\":[],\"quality_tier\":\"good\"}"}}]}`)
	}))
	defer server.Close()
	cfg := testConfig()
	cfg.Providers["openai"] = providerConfig{Model: "gpt-4.1", MaxTokens: 1200, BaseURL: server.URL}
	app := &application{httpClient: server.Client(), cfg: cfg}
	analysis, err := app.runLLMReview(context.Background(), "openai", evalContainerResult{SkillContent: "skill", SupportingContext: "ctx", Deterministic: map[string]any{"issues": []any{}}})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if analysis.Model != "gpt-4.1" {
		t.Fatalf("unexpected model: %+v", analysis)
	}
}

func TestRunOpenAIReviewWithGPT53ChatLatestUsesMaxCompletionTokens(t *testing.T) {
	oldKey := os.Getenv("OPENAI_API_KEY")
	defer func() {
		if oldKey == "" {
			_ = os.Unsetenv("OPENAI_API_KEY")
		} else {
			_ = os.Setenv("OPENAI_API_KEY", oldKey)
		}
	}()
	_ = os.Setenv("OPENAI_API_KEY", "configured-openai")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read body: %v", err)
		}
		jsonBody := string(body)
		if !strings.Contains(jsonBody, `"model":"gpt-5.3-chat-latest"`) || !strings.Contains(jsonBody, `"max_completion_tokens":1200`) || strings.Contains(jsonBody, `"max_tokens"`) {
			t.Fatalf("unexpected request body: %s", jsonBody)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"{\"strengths\":[\"Concise\"],\"weaknesses\":[],\"suggestions\":[],\"security_flags\":[],\"quality_tier\":\"good\"}"}}]}`)
	}))
	defer server.Close()
	cfg := testConfig()
	cfg.Providers["openai"] = providerConfig{Model: "gpt-5.3-chat-latest", MaxTokens: 1200, BaseURL: server.URL}
	app := &application{httpClient: server.Client(), cfg: cfg}
	analysis, err := app.runLLMReview(context.Background(), "openai", evalContainerResult{SkillContent: "skill", SupportingContext: "ctx", Deterministic: map[string]any{"issues": []any{}}})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if analysis.Model != "gpt-5.3-chat-latest" {
		t.Fatalf("unexpected model: %+v", analysis)
	}
}

func TestRunOpenAIReviewWithGPT54UsesMaxCompletionTokens(t *testing.T) {
	oldKey := os.Getenv("OPENAI_API_KEY")
	defer func() {
		if oldKey == "" {
			_ = os.Unsetenv("OPENAI_API_KEY")
		} else {
			_ = os.Setenv("OPENAI_API_KEY", oldKey)
		}
	}()
	_ = os.Setenv("OPENAI_API_KEY", "configured-openai")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("read body: %v", err)
		}
		jsonBody := string(body)
		if !strings.Contains(jsonBody, `"model":"gpt-5.4"`) || !strings.Contains(jsonBody, `"max_completion_tokens":1200`) || strings.Contains(jsonBody, `"max_tokens"`) {
			t.Fatalf("unexpected request body: %s", jsonBody)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"{\"strengths\":[\"Concise\"],\"weaknesses\":[],\"suggestions\":[],\"security_flags\":[],\"quality_tier\":\"good\"}"}}]}`)
	}))
	defer server.Close()
	cfg := testConfig()
	cfg.Providers["openai"] = providerConfig{Model: "gpt-5.4", MaxTokens: 1200, BaseURL: server.URL}
	app := &application{httpClient: server.Client(), cfg: cfg}
	analysis, err := app.runLLMReview(context.Background(), "openai", evalContainerResult{SkillContent: "skill", SupportingContext: "ctx", Deterministic: map[string]any{"issues": []any{}}})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if analysis.Model != "gpt-5.4" {
		t.Fatalf("unexpected model: %+v", analysis)
	}
}

func TestRunAnthropicReviewSupportsMultipleModels(t *testing.T) {
	oldKey := os.Getenv("ANTHROPIC_API_KEY")
	defer func() {
		if oldKey == "" {
			_ = os.Unsetenv("ANTHROPIC_API_KEY")
		} else {
			_ = os.Setenv("ANTHROPIC_API_KEY", oldKey)
		}
	}()
	for _, model := range []string{"claude-sonnet-4-6", "claude-haiku-4-5"} {
		_ = os.Setenv("ANTHROPIC_API_KEY", "configured-anthropic")
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			body, err := io.ReadAll(r.Body)
			if err != nil {
				t.Fatalf("read body: %v", err)
			}
			if !strings.Contains(string(body), `"model":"`+model+`"`) {
				t.Fatalf("expected anthropic model %q in request body: %s", model, string(body))
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"content":[{"type":"text","text":"{\"strengths\":[\"Clear scope\"],\"weaknesses\":[],\"suggestions\":[],\"security_flags\":[],\"quality_tier\":\"good\"}"}]}`)
		}))
		cfg := testConfig()
		cfg.Providers["anthropic"] = providerConfig{Model: model, MaxTokens: 1200, BaseURL: server.URL}
		app := &application{httpClient: server.Client(), cfg: cfg}
		analysis, err := app.runLLMReview(context.Background(), "anthropic", evalContainerResult{SkillContent: "skill", SupportingContext: "ctx", Deterministic: map[string]any{"issues": []any{}}})
		server.Close()
		if err != nil {
			t.Fatalf("unexpected error for model %q: %v", model, err)
		}
		if analysis.Model != model {
			t.Fatalf("unexpected analysis for model %q: %+v", model, analysis)
		}
	}
}

func TestRunLLMReviewWithOpenAI(t *testing.T) {
	oldKey := os.Getenv("OPENAI_API_KEY")
	defer func() {
		if oldKey == "" {
			_ = os.Unsetenv("OPENAI_API_KEY")
		} else {
			_ = os.Setenv("OPENAI_API_KEY", oldKey)
		}
	}()
	_ = os.Setenv("OPENAI_API_KEY", "configured-openai")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer configured-openai" {
			t.Fatalf("unexpected auth header: %q", got)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"choices":[{"message":{"role":"assistant","content":"{\"strengths\":[\"Concise\"],\"weaknesses\":[],\"suggestions\":[],\"security_flags\":[],\"quality_tier\":\"good\"}"}}]}`)
	}))
	defer server.Close()
	cfg := testConfig()
	cfg.Providers["openai"] = providerConfig{Model: "gpt-4.1-nano", MaxTokens: 1200, BaseURL: server.URL}
	app := &application{httpClient: server.Client(), cfg: cfg}
	analysis, err := app.runLLMReview(context.Background(), "openai", evalContainerResult{SkillContent: "skill", SupportingContext: "ctx", Deterministic: map[string]any{"issues": []any{}}})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if analysis.Provider != "openai" || analysis.Model != "gpt-4.1-nano" || analysis.QualityTier != "good" {
		t.Fatalf("unexpected analysis: %+v", analysis)
	}
}

func TestSupportedLLMProvider(t *testing.T) {
	for _, provider := range []string{"anthropic", "openai", "gemini", "groq"} {
		if !isSupportedLLMProvider(provider) {
			t.Fatalf("expected provider %q to be supported", provider)
		}
	}
	if isSupportedLLMProvider("unknown") {
		t.Fatal("expected unknown provider to be rejected")
	}
}

func TestHandleVersion(t *testing.T) {
	buildVersion = "test-version"
	buildCommit = "abcdef123456"
	rr := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/version", nil)
	app := &application{cfg: testConfig()}
	app.handleVersion(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "test-version") || !strings.Contains(rr.Body.String(), "abcdef123456") {
		t.Fatalf("unexpected body: %s", rr.Body.String())
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
	app := &application{cfg: testConfig()}
	if _, _, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1"}, `not-json`); err == nil {
		t.Fatal("expected parse error")
	}
	if _, _, err := app.finalizeEvaluation(context.Background(), evalJob{JobID: "job-1"}, `{"status":"error","message":"ANTHROPIC_API_KEY=secret"}`); err == nil || strings.Contains(err.Error(), "secret") {
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

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), cfg: testConfig()}
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

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), cfg: testConfig()}
	app.handleUpload(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "not an allowed upload type") {
		t.Fatalf("unexpected body: %s", rr.Body.String())
	}
}

func TestHandleUploadRejectsMixedSources(t *testing.T) {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	part, err := writer.CreateFormFile("files", "skill/SKILL.md")
	if err != nil {
		t.Fatalf("create form file: %v", err)
	}
	if _, err := part.Write([]byte("# skill")); err != nil {
		t.Fatalf("write form file: %v", err)
	}
	if err := writer.WriteField("githubUrl", "https://github.com/org/repo/blob/main/SKILL.md"); err != nil {
		t.Fatalf("write field: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("close writer: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/upload", &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	rr := httptest.NewRecorder()

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), cfg: testConfig()}
	app.handleUpload(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "choose either uploaded files or a GitHub URL") {
		t.Fatalf("unexpected body: %s", rr.Body.String())
	}
}

func TestHandleUploadSurfacesGitHubNotFoundAsBadRequest(t *testing.T) {
	oldTransport := http.DefaultTransport
	defer func() { http.DefaultTransport = oldTransport }()

	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.NotFound(w, r)
	}))
	defer server.Close()

	transport := server.Client().Transport
	http.DefaultTransport = roundTripFunc(func(req *http.Request) (*http.Response, error) {
		if req.URL.Host == "raw.githubusercontent.com" {
			req.URL.Scheme = "https"
			req.URL.Host = strings.TrimPrefix(server.URL, "https://")
		}
		return transport.RoundTrip(req)
	})

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if err := writer.WriteField("githubUrl", "https://github.com/org/repo/blob/main/SKILL.md"); err != nil {
		t.Fatalf("write field: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("close writer: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/upload", &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	rr := httptest.NewRecorder()

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), cfg: testConfig()}
	app.handleUpload(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d body=%s", rr.Code, rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), "could not be found") {
		t.Fatalf("unexpected body: %s", rr.Body.String())
	}
}

func TestHandleUploadSurfacesGitHubRateLimitAsServiceUnavailable(t *testing.T) {
	oldTransport := http.DefaultTransport
	defer func() { http.DefaultTransport = oldTransport }()

	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer server.Close()

	transport := server.Client().Transport
	http.DefaultTransport = roundTripFunc(func(req *http.Request) (*http.Response, error) {
		if req.URL.Host == "raw.githubusercontent.com" {
			req.URL.Scheme = "https"
			req.URL.Host = strings.TrimPrefix(server.URL, "https://")
		}
		return transport.RoundTrip(req)
	})

	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	if err := writer.WriteField("githubUrl", "https://github.com/org/repo/blob/main/SKILL.md"); err != nil {
		t.Fatalf("write field: %v", err)
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("close writer: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/upload", &body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	rr := httptest.NewRecorder()

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond}), cfg: testConfig()}
	app.handleUpload(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d body=%s", rr.Code, rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), "temporarily unavailable") {
		t.Fatalf("unexpected body: %s", rr.Body.String())
	}
}

func TestLimitMiddlewareRejectsHourlyEvaluationBurst(t *testing.T) {
	ctx := context.Background()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	app := &application{rdb: rdb, cfg: testConfig()}

    handler := app.uploadAbuseProtection(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	ip := "198.51.100.10"
	if err := rdb.Set(ctx, "ratelimit:hourly:"+ip, rateLimitHourlyMax, time.Hour).Err(); err != nil {
		t.Fatalf("seed hourly rate limit: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/upload", strings.NewReader("enableLlm=false"))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.RemoteAddr = ip + ":1234"
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("unexpected status: %d body=%s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "hourly evaluation rate limit exceeded") {
		t.Fatalf("unexpected body: %s", rec.Body.String())
	}
}

func TestLimitMiddlewareRejectsDailyEvaluationBurst(t *testing.T) {
	ctx := context.Background()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	app := &application{rdb: rdb, cfg: testConfig()}

    handler := app.uploadAbuseProtection(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	ip := "198.51.100.20"
	if err := rdb.Set(ctx, "ratelimit:daily:"+ip, rateLimitDailyMax, 24*time.Hour).Err(); err != nil {
		t.Fatalf("seed daily rate limit: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/upload", strings.NewReader("enableLlm=false"))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.RemoteAddr = ip + ":1234"
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("unexpected status: %d body=%s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "daily evaluation rate limit exceeded") {
		t.Fatalf("unexpected body: %s", rec.Body.String())
	}
}

func TestLimitMiddlewareKeepsStrictLLMLimit(t *testing.T) {
	ctx := context.Background()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	app := &application{rdb: rdb, cfg: testConfig()}

    handler := app.uploadAbuseProtection(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))

	ip := "198.51.100.30"
	if err := rdb.Set(ctx, "ratelimit:llm:"+ip, llmRateLimitMax, 24*time.Hour).Err(); err != nil {
		t.Fatalf("seed llm rate limit: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, "/upload", strings.NewReader("enableLlm=true"))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.RemoteAddr = ip + ":1234"
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("unexpected status: %d body=%s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "optional AI review rate limit exceeded") {
		t.Fatalf("unexpected body: %s", rec.Body.String())
	}
}

func TestStripMarkdownFences(t *testing.T) {
	tests := []struct {
		name  string
		input string
		want  string
	}{
		{"plain json", `{"strengths":["good"]}`, `{"strengths":["good"]}`},
		{"json fenced", "```json\n{\"strengths\":[\"good\"]}\n```", `{"strengths":["good"]}`},
		{"JSON uppercase fenced", "```JSON\n{\"strengths\":[\"good\"]}\n```", `{"strengths":["good"]}`},
		{"plain fenced", "```\n{\"strengths\":[\"good\"]}\n```", `{"strengths":["good"]}`},
		{"whitespace padding", "  ```json\n{\"strengths\":[\"good\"]}\n```  ", `{"strengths":["good"]}`},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := stripMarkdownFences(tc.input)
			if got != tc.want {
				t.Fatalf("stripMarkdownFences(%q) = %q, want %q", tc.input, got, tc.want)
			}
		})
	}
}
