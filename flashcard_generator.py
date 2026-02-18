#!/usr/bin/env python3
"""Generate Anki flashcards from PDF sections using Claude API."""

import argparse
import csv
import json
import os
import re
import sys

import anthropic
import pymupdf


SYSTEM_PROMPT = """You are an expert flashcard creator. Your task is to generate high-quality Anki flashcards from the provided text.

Rules:
- Each flashcard should test ONE atomic fact or concept.
- The front should be a clear, specific question.
- The back should be a concise, complete answer.
- Avoid yes/no questions. Prefer "what", "how", "why", "explain" questions.
- Cover the most important concepts, definitions, formulas, and relationships.
- Do not create trivial or overly obvious cards.
- Output ONLY a JSON array of objects with "front" and "back" keys. No other text."""

USER_PROMPT_TEMPLATE = """Create flashcards from this section titled "{title}":

{text}

Return a JSON array of flashcard objects, each with "front" and "back" keys."""

MAX_SECTION_CHARS = 80_000
CHUNK_OVERLAP = 2_000


def extract_sections_from_toc(doc: pymupdf.Document) -> list[dict]:
    """Extract sections using the PDF's table of contents."""
    toc = doc.get_toc()
    if not toc:
        return []

    sections = []
    for i, (level, title, page_num) in enumerate(toc):
        # Determine page range for this TOC entry
        start_page = page_num - 1  # pymupdf uses 0-indexed pages
        if i + 1 < len(toc):
            end_page = toc[i + 1][2] - 1  # up to next entry's start
        else:
            end_page = len(doc)

        text = ""
        for pg in range(max(0, start_page), min(end_page, len(doc))):
            text += doc[pg].get_text()

        text = text.strip()
        if text:
            sections.append({"title": title.strip(), "text": text})

    return sections


def extract_sections_by_font_size(doc: pymupdf.Document) -> list[dict]:
    """Fallback: detect headings by font size heuristics."""
    all_spans = []
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"].strip():
                        all_spans.append(span)

    if not all_spans:
        return []

    # Find the median font size to identify headings (larger than median)
    sizes = [s["size"] for s in all_spans]
    sizes.sort()
    median_size = sizes[len(sizes) // 2]
    heading_threshold = median_size * 1.2

    sections = []
    current_title = "Introduction"
    current_text = []

    for span in all_spans:
        text = span["text"].strip()
        if span["size"] >= heading_threshold and len(text) < 200:
            # This looks like a heading
            if current_text:
                sections.append({
                    "title": current_title,
                    "text": "\n".join(current_text),
                })
            current_title = text
            current_text = []
        else:
            current_text.append(text)

    # Don't forget the last section
    if current_text:
        sections.append({
            "title": current_title,
            "text": "\n".join(current_text),
        })

    return sections


def extract_sections(pdf_path: str) -> list[dict]:
    """Extract titled sections from a PDF file."""
    doc = pymupdf.open(pdf_path)

    sections = extract_sections_from_toc(doc)
    if not sections:
        print("No table of contents found, detecting headings by font size...")
        sections = extract_sections_by_font_size(doc)

    if not sections:
        # Last resort: treat entire document as one section
        text = ""
        for page in doc:
            text += page.get_text()
        sections = [{"title": "Full Document", "text": text.strip()}]

    doc.close()
    return sections


def chunk_text(text: str, max_chars: int = MAX_SECTION_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks if it exceeds max_chars."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        chunks.append(chunk)
        start = end - overlap
    return chunks


def generate_flashcards(client: anthropic.Anthropic, model: str, title: str, text: str, max_cards: int | None) -> list[dict]:
    """Send a section to Claude and parse flashcard JSON response."""
    chunks = chunk_text(text)
    all_cards = []

    for i, chunk in enumerate(chunks):
        chunk_title = title if len(chunks) == 1 else f"{title} (part {i + 1}/{len(chunks)})"

        prompt = USER_PROMPT_TEMPLATE.format(title=chunk_title, text=chunk)
        if max_cards:
            remaining = max_cards - len(all_cards)
            if remaining <= 0:
                break
            prompt += f"\n\nGenerate at most {remaining} flashcards."

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.content[0].text

        # Extract JSON array from the response
        match = re.search(r"\[.*\]", response_text, re.DOTALL)
        if not match:
            print(f"  Warning: Could not parse JSON from response for '{chunk_title}', skipping.")
            continue

        try:
            cards = json.loads(match.group())
            all_cards.extend(cards)
        except json.JSONDecodeError:
            print(f"  Warning: Invalid JSON for '{chunk_title}', skipping.")

    return all_cards


def sanitize_tag(title: str) -> str:
    """Convert a section title into a valid Anki tag (no spaces)."""
    tag = re.sub(r"[^\w\-]", "_", title)
    tag = re.sub(r"_+", "_", tag).strip("_")
    return tag


def write_tsv(cards: list[dict], output_path: str):
    """Write flashcards to a TSV file importable by Anki."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["Front", "Back", "Tags"])
        for card in cards:
            writer.writerow([card["front"], card["back"], card.get("tag", "")])


def main():
    parser = argparse.ArgumentParser(description="Generate Anki flashcards from a PDF using Claude.")
    parser.add_argument("pdf", help="Path to the input PDF file")
    parser.add_argument("-o", "--output", help="Output TSV file path (default: <pdf_name>_flashcards.tsv)")
    parser.add_argument("--api-key", help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")
    parser.add_argument("--model", default="claude-sonnet-4-5-20250929", help="Claude model to use")
    parser.add_argument("--max-cards", type=int, help="Maximum flashcards per section")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"Error: File not found: {args.pdf}")
        sys.exit(1)

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: Provide an API key via --api-key or ANTHROPIC_API_KEY env var.")
        sys.exit(1)

    output_path = args.output or os.path.splitext(args.pdf)[0] + "_flashcards.tsv"

    # Step 1: Extract sections
    print(f"Extracting sections from: {args.pdf}")
    sections = extract_sections(args.pdf)
    print(f"Found {len(sections)} section(s).\n")

    # Step 2: Generate flashcards per section
    client = anthropic.Anthropic(api_key=api_key)
    all_cards = []

    for i, section in enumerate(sections, 1):
        title = section["title"]
        print(f"[{i}/{len(sections)}] Generating flashcards for: {title} ({len(section['text'])} chars)")

        cards = generate_flashcards(client, args.model, title, section["text"], args.max_cards)
        tag = sanitize_tag(title)
        for card in cards:
            card["tag"] = tag

        all_cards.extend(cards)
        print(f"  -> {len(cards)} cards generated.\n")

    # Step 3: Write output
    if all_cards:
        write_tsv(all_cards, output_path)
        print(f"Done! {len(all_cards)} flashcards written to: {output_path}")
    else:
        print("No flashcards were generated.")


if __name__ == "__main__":
    main()
