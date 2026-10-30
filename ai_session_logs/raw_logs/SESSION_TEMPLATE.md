# AI Session Raw Log Template

**File:** `SESSION_NNN.md`  
**Purpose:** Record a single Antigravity (or AI assistant) prompt/response iteration verbatim.  
**Usage:** Copy this file, rename it `SESSION_001.md` (etc.), and fill in the actual content immediately after the session. Never reconstruct from memory; if you did not save the session, leave the section blank and note "session not captured."

---

## Session Metadata

| Field | Value |
|-------|-------|
| Session number | NNN |
| Date/time | YYYY-MM-DDTHH:MM:SS+TZ |
| Tool | Antigravity / other |
| Model | (e.g., Claude Sonnet, Gemini) |
| Task context | (e.g., "Implementing checkout_handler.py idempotency") |

---

## Prompt (verbatim or paraphrased)

```
[Paste or paraphrase the exact prompt you sent to the AI here]
```

---

## AI Response Summary

```
[Paste the key parts of the AI's response — code snippets, explanations, or design decisions]
```

---

## Code / Output Produced

```python
# Paste generated code here if applicable
```

---

## Review Notes

*What did you check after receiving the AI output? What looked correct or suspicious?*

---

## Defect Found (if any)

| Field | Value |
|-------|-------|
| Defect description | |
| Affected file/line | |
| Evidence (error or test output) | |

---

## Corrective Action

*What follow-up prompt or manual edit was applied?*

---

## Verified Resolution

*Which test passed, or what output confirmed the fix?*

---

> **Reminder:** Only save content that actually occurred. Empty sections are better than invented content.
