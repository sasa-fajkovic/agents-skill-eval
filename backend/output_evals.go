package main

import "encoding/json"

type outputFixtureSummary struct {
	Measurement string              `json:"measurement"`
	Files       []outputFixtureFile `json:"files"`
}

type outputFixtureFile struct {
	Path           string `json:"path"`
	Valid          bool   `json:"valid"`
	EvalCount      int    `json:"eval_count"`
	AssertionCount int    `json:"assertion_count"`
	InputFileCount int    `json:"input_file_count"`
}

func parseOutputFixtureSummary(data json.RawMessage) *outputFixtureSummary {
	if len(data) > 4096 {
		return nil
	}
	var input struct {
		Measurement string `json:"measurement"`
		Files       *[]struct {
			Path           string `json:"path"`
			Valid          *bool  `json:"valid"`
			EvalCount      *int   `json:"eval_count"`
			AssertionCount *int   `json:"assertion_count"`
			InputFileCount *int   `json:"input_file_count"`
		} `json:"files"`
	}
	if json.Unmarshal(data, &input) != nil || input.Measurement != "not_run" || input.Files == nil || len(*input.Files) > 1 {
		return nil
	}
	summary := outputFixtureSummary{Measurement: "not_run", Files: make([]outputFixtureFile, 0, len(*input.Files))}
	for _, file := range *input.Files {
		if file.Path != "evals/evals.json" || file.Valid == nil || file.EvalCount == nil || file.AssertionCount == nil || file.InputFileCount == nil {
			return nil
		}
		for _, count := range []int{*file.EvalCount, *file.AssertionCount, *file.InputFileCount} {
			if count < 0 || count > 1_000_000 {
				return nil
			}
		}
		if (*file.Valid && *file.EvalCount == 0) || (!*file.Valid && (*file.EvalCount != 0 || *file.AssertionCount != 0 || *file.InputFileCount != 0)) {
			return nil
		}
		summary.Files = append(summary.Files, outputFixtureFile{file.Path, *file.Valid, *file.EvalCount, *file.AssertionCount, *file.InputFileCount})
	}
	return &summary
}
