---
name: huggingface-readme
description: Locate and understand model README / model card for any LLM hosted on Hugging Face.
---

# Hugging Face Model README Lookup

## Purpose

Help the user find, fetch, and understand the README (model card) for any Large
Language Model (LLM) hosted on Hugging Face. The model card is the primary source
of truth for understanding a model's capabilities, limitations, intended use,
training data, evaluation results, and licensing.

## Instructions

When the user asks about an LLM on Hugging Face — or asks you to find information
about a model — follow these steps:

### Step 1 — Resolve the model identifier

A Hugging Face model identifier has the form `{owner}/{model-name}`, for example
`google/gemma-3-27b-it` or `meta-llama/Llama-3.1-8B-Instruct`.

- If the user gives a full identifier, use it directly.
- If the user gives only a model name (e.g. "Gemma 3 27B"), search for it:
  - Fetch `https://huggingface.co/api/models?search={query}&sort=downloads&direction=-1&limit=5`
  - Pick the best match from the results by comparing the `id` field.
- If the user is vague (e.g. "the latest Llama model"), use your knowledge to
  narrow candidates, then confirm with the search API above.

### Step 2 — Fetch the model card (README)

The raw README.md for any model is available at:

```
https://huggingface.co/{owner}/{model-name}/raw/main/README.md
```

Fetch that URL. The file is Markdown with YAML frontmatter. Both sections contain
important information.

### Step 3 — Parse the YAML frontmatter

The frontmatter (between the opening and closing `---` lines) contains structured
metadata. Key fields to extract and surface:

| Field | What it tells you |
|---|---|
| `license` / `license_name` | The model's license (e.g. `apache-2.0`, `gemma`, `llama3.1`). |
| `language` | Languages the model supports. |
| `pipeline_tag` | Primary task (e.g. `text-generation`, `text2text-generation`). |
| `tags` | Extra labels — look for quantization info, fine-tune tags, etc. |
| `library_name` | Framework the model targets (`transformers`, `vllm`, etc.). |
| `base_model` | The model this was fine-tuned or derived from. |
| `datasets` | Training or evaluation datasets used. |
| `model-index` | Benchmark results in a structured format. |
| `quantized_by` / `extra_gated_*` | Access restrictions or quantization provenance. |

### Step 4 — Parse the Markdown body

After the frontmatter, the Markdown body is the human-readable model card. Look
for the following common sections and summarize them for the user:

1. **Model Summary / Description** — What the model is and what it does.
2. **Intended Use & Limitations** — Recommended use cases and known failure modes.
3. **How to Use / Quick Start** — Code snippets showing how to load and run the model.
4. **Training Details** — Architecture, training data, hyperparameters.
5. **Evaluation / Benchmarks** — Performance numbers on standard benchmarks.
6. **Technical Specifications** — Context length, parameter count, vocabulary size, supported hardware.
7. **License** — Terms of use, acceptable use policies.
8. **Citation** — BibTeX or paper references.

Not every model card has all sections. Summarize what is available.

### Step 5 — Present a clear summary

After fetching and parsing, present the user with a structured summary:

```
## {Model Name}

**Identifier:** {owner}/{model-name}
**License:** {license}
**Pipeline:** {pipeline_tag}
**Parameters:** {if available}
**Context Length:** {if available}
**Languages:** {languages}
**Base Model:** {if fine-tuned}

### Overview
{1-3 sentence summary of what the model does}

### Key Capabilities
{Bullet list of strengths / intended uses}

### Limitations
{Bullet list of known limitations or restrictions}

### Benchmarks
{Table of key benchmark results, if available}

### Quick Start
{Shortest code snippet to load and run the model}
```

Adjust the template based on what the model card actually contains. Omit sections
that have no data rather than showing empty placeholders.

## Tips

- **Gated models**: Some models require accepting terms on the HF website before
  access is granted. If the README fetch fails with a 403, tell the user the model
  is gated and they need to request access at
  `https://huggingface.co/{owner}/{model-name}`.
- **Quantized variants**: Users often want GGUF or AWQ versions. These are
  typically hosted under a different owner (e.g. `TheBloke/{model}-GGUF`). Offer
  to look those up if relevant.
- **Comparing models**: If the user wants to compare two models, fetch both READMEs
  and present a side-by-side table of key attributes and benchmarks.
- **Stale info**: Model cards can lag behind actual capabilities. If your training
  data contains more recent information about a model, mention both sources.
