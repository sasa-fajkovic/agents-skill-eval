package main

import (
	"context"
	"encoding/json"
	"errors"
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

func TestParseLLMJSON(t *testing.T) {
	t.Run("parses raw json", func(t *testing.T) {
		parsed, err := parseLLMJSON(`{"quality_tier":"good"}`)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if parsed["quality_tier"] != "good" {
			t.Fatalf("unexpected parsed content: %#v", parsed)
		}
	})

	t.Run("parses fenced json", func(t *testing.T) {
		parsed, err := parseLLMJSON("```json\n{\"quality_tier\":\"excellent\"}\n```")
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if parsed["quality_tier"] != "excellent" {
			t.Fatalf("unexpected parsed content: %#v", parsed)
		}
	})

	t.Run("rejects invalid json", func(t *testing.T) {
		_, err := parseLLMJSON("not json")
		if err == nil || !strings.Contains(err.Error(), "invalid JSON") {
			t.Fatalf("expected invalid json error, got %v", err)
		}
	})
}

func TestAnyToStringSlice(t *testing.T) {
	got := anyToStringSlice([]any{" one ", "", 2, nil})
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
	analysis := llmAnalysis{QualityTier: "excellent"}
	if got := computeOverallScore(map[string]any{"issues": []any{"a", "b"}}, analysis); got != 85 {
		t.Fatalf("unexpected score: %d", got)
	}
	if got := computeOverallScore(map[string]any{"issues": []any{"a", "b", "c", "d", "e", "f", "g"}}, llmAnalysis{QualityTier: "unknown"}); got != 30 {
		t.Fatalf("unexpected clamped fallback score: %d", got)
	}
}

func TestSummarizeIssues(t *testing.T) {
	if got := summarizeIssues(map[string]any{"issues": []any{"first issue", "second"}}, llmAnalysis{}); !strings.Contains(got, "Primary issue: first issue") {
		t.Fatalf("unexpected summary: %q", got)
	}
	if got := summarizeIssues(map[string]any{"issues": []any{}}, llmAnalysis{Strengths: []string{"Strong structure"}}); !strings.Contains(got, "Key strength: Strong structure") {
		t.Fatalf("unexpected summary: %q", got)
	}
	if got := summarizeIssues(map[string]any{}, llmAnalysis{}); !strings.Contains(got, "no deterministic issues") {
		t.Fatalf("unexpected summary: %q", got)
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
	if err := validateFileType("bad.exe", "application/octet-stream"); err == nil {
		t.Fatal("expected exe rejection")
	}
	if err := validateFileType("script.sh", "image/png"); err == nil {
		t.Fatal("expected shell mime rejection")
	}
}

func TestFinalizeEvaluation(t *testing.T) {
	oldRunner := hostLLMAnalysisRunner
	defer func() { hostLLMAnalysisRunner = oldRunner }()

	hostLLMAnalysisRunner = func(ctx context.Context, skillContent, supportingContext string) (llmAnalysis, error) {
		if !strings.Contains(skillContent, "skill body") {
			t.Fatalf("unexpected skill content: %q", skillContent)
		}
		if supportingContext != "extra context" {
			t.Fatalf("unexpected supporting context: %q", supportingContext)
		}
		return llmAnalysis{Strengths: []string{"Good guardrails"}, QualityTier: "good"}, nil
	}

	app := &application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond})}
	raw := `{"status":"ok","skill_name":"demo","skill_content":"skill body","supporting_context":"extra context","deterministic":{"issues":["missing tests"]}}`

	result, err := app.finalizeEvaluation(context.Background(), "job-1", raw)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result, `"overall_score":75`) || !strings.Contains(result, `"skill_name":"demo"`) {
		t.Fatalf("unexpected final result: %s", result)
	}
}

func TestFinalizeEvaluationErrors(t *testing.T) {
	app := &application{}
	if _, err := app.finalizeEvaluation(context.Background(), "job-1", `not-json`); err == nil {
		t.Fatal("expected parse error")
	}
	if _, err := app.finalizeEvaluation(context.Background(), "job-1", `{"status":"error","message":"ANTHROPIC_API_KEY=secret"}`); err == nil || strings.Contains(err.Error(), "secret") {
		t.Fatalf("expected redacted evaluator error, got %v", err)
	}

	oldRunner := hostLLMAnalysisRunner
	defer func() { hostLLMAnalysisRunner = oldRunner }()
	hostLLMAnalysisRunner = func(ctx context.Context, skillContent, supportingContext string) (llmAnalysis, error) {
		return llmAnalysis{}, errors.New("llm failed")
	}
	result, err := (&application{rdb: redis.NewClient(&redis.Options{Addr: "127.0.0.1:0", DialTimeout: time.Millisecond, ReadTimeout: time.Millisecond, WriteTimeout: time.Millisecond})}).finalizeEvaluation(context.Background(), "job-1", `{"status":"ok","skill_name":"demo","skill_content":"body","deterministic":{}}`)
	if err != nil {
		t.Fatalf("expected fallback result, got error %v", err)
	}
	if !strings.Contains(result, "LLM analysis unavailable: llm failed") {
		t.Fatalf("expected fallback weakness in result, got %s", result)
	}
}
