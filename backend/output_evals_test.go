package main

import (
	"encoding/json"
	"strings"
	"testing"
)

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
