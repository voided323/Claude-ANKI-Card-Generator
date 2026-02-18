# PDF to Anki Flashcard Generator

A Python CLI tool that extracts sections from a PDF and uses the Claude API to generate Anki-importable flashcards.

## How It Works

The program runs in four stages:

### 1. PDF Section Extraction

The tool opens the PDF with PyMuPDF and identifies sections using a layered approach:

- **Table of Contents (preferred)** — If the PDF has a TOC, sections are split by TOC entries with their page ranges.
- **Font-size heuristic (fallback)** — Scans for text spans with a font size at least 1.2x the median, treating them as headings.
- **Whole document (last resort)** — If no structure is detected, the entire PDF is treated as a single section.

### 2. Chunking

Sections longer than 80,000 characters are split into overlapping chunks (2,000-character overlap) so they fit within Claude's context window without losing continuity.

### 3. Flashcard Generation

Each section is sent to the Claude API with a prompt that instructs it to:

- Create one flashcard per atomic fact or concept
- Use clear, specific questions (no yes/no)
- Return a JSON array of `{"front": "...", "back": "..."}` objects

### 4. TSV Output

Results are written to a tab-separated file with three columns:

| Column | Description |
|--------|-------------|
| Front  | The question |
| Back   | The answer |
| Tags   | Section title (sanitized for Anki) |

## Setup

### Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com)

### Installation

```bash
pip install -r requirements.txt
```

### API Key

Get your key from [console.anthropic.com](https://console.anthropic.com) under **Settings > API Keys**.

Set it as an environment variable:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or pass it directly with the `--api-key` flag.

## Usage

```bash
python3 flashcard_generator.py <pdf_file> [options]
```

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `pdf` | Path to the input PDF file | *(required)* |
| `-o`, `--output` | Output TSV file path | `<pdf_name>_flashcards.tsv` |
| `--api-key` | Anthropic API key | `ANTHROPIC_API_KEY` env var |
| `--model` | Claude model to use | `claude-sonnet-4-5-20250929` |
| `--max-cards` | Max flashcards per section | unlimited |

### Examples

```bash
# Basic usage
python3 flashcard_generator.py textbook.pdf

# Custom output path, limit cards per section
python3 flashcard_generator.py textbook.pdf -o cards.tsv --max-cards 10

# Use a specific model
python3 flashcard_generator.py textbook.pdf --model claude-haiku-4-5-20251001
```

## Importing into Anki

1. Open Anki and go to **File > Import**
2. Select the generated `.tsv` file
3. Set the separator to **Tab**
4. Map the three fields: **Front**, **Back**, **Tags**
5. Click **Import**

## Project Files

| File | Purpose |
|------|---------|
| `flashcard_generator.py` | Main application |
| `requirements.txt` | Python dependencies |
| `README.md` | This file |

