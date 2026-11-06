package main

import (
	"bytes"
	"encoding/json"
	"mime/multipart"
	"net/http/httptest"
	"net/textproto"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMultipartFixturePathsArePreservedAndValidated(t *testing.T) {
	for _, filename := range []string{"quality-smoke/SKILL.md", "quality-smoke/evals/evals.json", "quality-smoke/evals/files/sales.csv", `quality-smoke\evals\files\sales.csv`, "../escape.csv", "quality-smoke/../escape.csv"} {
		t.Run(filename, func(t *testing.T) {
			var body bytes.Buffer
			writer := multipart.NewWriter(&body)
			part, err := writer.CreateFormFile("files", filename)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := part.Write([]byte("fixture content")); err != nil {
				t.Fatal(err)
			}
			if err := writer.Close(); err != nil {
				t.Fatal(err)
			}
			request := httptest.NewRequest("POST", "/upload", &body)
			request.Header.Set("Content-Type", writer.FormDataContentType())
			if err := request.ParseMultipartForm(5 << 20); err != nil {
				t.Fatal(err)
			}
			defer request.MultipartForm.RemoveAll()
			root := t.TempDir()
			err = saveValidatedFile(root, request.MultipartForm.File["files"][0])
			if strings.Contains(filename, "..") {
				if err == nil {
					t.Fatal("accepted raw multipart path traversal")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			data, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(strings.ReplaceAll(filename, `\`, "/"))))
			if err != nil || string(data) != "fixture content" {
				t.Fatalf("nested fixture path or contents lost: %q, %v", data, err)
			}
		})
	}
}

func TestMultipartFixtureBasenameAndNormalizedPathCollisions(t *testing.T) {
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	names := []string{"quality/evals/files/input.csv", "quality/references/input.csv", `quality\evals\files\input.csv`}
	for index, filename := range names {
		part, err := writer.CreateFormFile("files", filename)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := part.Write([]byte{byte('A' + index)}); err != nil {
			t.Fatal(err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest("POST", "/upload", &body)
	request.Header.Set("Content-Type", writer.FormDataContentType())
	if err := request.ParseMultipartForm(5 << 20); err != nil {
		t.Fatal(err)
	}
	defer request.MultipartForm.RemoveAll()
	root := t.TempDir()
	for index, header := range request.MultipartForm.File["files"] {
		err := saveValidatedFile(root, header)
		if index == 2 {
			if err == nil || !strings.Contains(err.Error(), "duplicate upload path") {
				t.Fatalf("expected normalized collision rejection, got %v", err)
			}
		} else if err != nil {
			t.Fatal(err)
		}
	}
	for index, filename := range names[:2] {
		data, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(filename)))
		if err != nil || string(data) != string([]byte{byte('A' + index)}) {
			t.Fatalf("fixture collision changed stored data: %q, %v", data, err)
		}
	}
}

func TestMultipartFixtureRejectsMalformedMetadata(t *testing.T) {
	for _, value := range []string{`form-data; name="files"; filename="unterminated`, `invalid`, `form-data; name="files"`} {
		header := &multipart.FileHeader{Filename: "SKILL.md", Header: textproto.MIMEHeader{"Content-Disposition": []string{value}}}
		if err := saveValidatedFile(t.TempDir(), header); err == nil || !strings.Contains(err.Error(), "invalid multipart file metadata") {
			t.Fatalf("expected malformed metadata rejection, got %v", err)
		}
	}
}

func TestOutputFixtureSummary(t *testing.T) {
	valid := `{"measurement":"not_run","files":[{"path":"evals/evals.json","valid":true,"eval_count":2,"assertion_count":3,"input_file_count":1,"prompt":"PRIVATE_PROMPT"}],"benchmark":{"pass_rate":1}}`
	summary := parseOutputFixtureSummary(json.RawMessage(valid))
	if summary == nil || len(summary.Files) != 1 || summary.Files[0].EvalCount != 2 {
		t.Fatalf("expected static summary, got %+v", summary)
	}
	encoded, err := json.Marshal(summary)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "PRIVATE_PROMPT") || strings.Contains(string(encoded), "pass_rate") {
		t.Fatalf("summary includes untrusted details: %s", encoded)
	}
	for _, raw := range []string{
		`{"measurement":"not_run","files":[]}`,
		`{"measurement":"not_run","files":[{"path":"evals/evals.json","valid":false,"eval_count":0,"assertion_count":0,"input_file_count":0}]}`,
	} {
		if parseOutputFixtureSummary(json.RawMessage(raw)) == nil {
			t.Fatalf("expected valid absence or invalid-fixture summary: %s", raw)
		}
	}
	for _, raw := range []string{
		`{}`,
		`{"measurement":"not_run","files":null}`,
		`{"measurement":"measured","files":[]}`,
		strings.Replace(valid, `"eval_count":2`, `"eval_count":-1`, 1),
		strings.Replace(valid, `"eval_count":2`, `"eval_count":1000001`, 1),
		strings.Replace(valid, `"eval_count":2`, `"eval_count":0`, 1),
		strings.Replace(valid, `"eval_count":2`, `"eval_count":"2"`, 1),
		strings.Replace(valid, `"eval_count":2,`, ``, 1),
		strings.Replace(valid, `"valid":true`, `"valid":false`, 1),
		strings.Replace(valid, `evals/evals.json`, `PRIVATE_PATH`, 1),
		strings.Repeat(" ", 4097),
	} {
		if parseOutputFixtureSummary(json.RawMessage(raw)) != nil {
			t.Fatalf("accepted invalid fixture summary: %.200s", raw)
		}
	}
}

func TestOutputFixturePromptUsesOnlyValidatedStaticSummary(t *testing.T) {
	var container evalContainerResult
	raw := `{"metadata":{"output_evals":{"measurement":"not_run","files":[{"path":"evals/evals.json","valid":true,"eval_count":2,"assertion_count":0,"input_file_count":1,"prompt":"PRIVATE_PROMPT"}],"pass_rate":1},"private":"PRIVATE_METADATA"}}`
	if err := json.Unmarshal([]byte(raw), &container); err != nil {
		t.Fatal(err)
	}
	prompt := buildLLMPrompt(container)
	if !strings.Contains(prompt, `"measurement":"not_run"`) || !strings.Contains(prompt, `"eval_count":2`) {
		t.Fatalf("missing static output fixture summary: %s", prompt)
	}
	for _, forbidden := range []string{"PRIVATE_PROMPT", "PRIVATE_METADATA", "pass_rate"} {
		if strings.Contains(prompt, forbidden) {
			t.Fatalf("prompt leaked metadata field %q: %s", forbidden, prompt)
		}
	}
	for _, raw := range []string{
		`{"metadata":{"output_evals":{"measurement":"measured","files":[]}}}`,
		`{"metadata":{"output_evals":{"measurement":"not_run","files":[{"path":"PRIVATE_PATH","valid":true,"eval_count":1,"assertion_count":0,"input_file_count":0}]}}}`,
	} {
		if err := json.Unmarshal([]byte(raw), &container); err != nil {
			t.Fatal(err)
		}
		if strings.Contains(buildLLMPrompt(container), "## Output Eval Summary") {
			t.Fatalf("accepted invalid summary: %s", raw)
		}
	}
}
