# Knowledge base

`guidelines/` holds short guideline documents for the RAG service: one topic per document,
paraphrased in plain language from official sources (government bodies and WHO), never news
articles or explainers. Each one cites its source in its front matter. They are reviewed before
they are indexed.

## Authoring rules

- **Front matter:** `id`, `title`, `topics`, `activities` (list or `all`), `audience`, `severity`,
  `source_name`, `source_url` (where it was fetched), `retrieved`, and, where useful,
  `original_title` and `aliases`.
- **Length:** about 150-400 words. A document whose source is short (e.g. the CPCB NAQI band
  statements) may be shorter, and is never padded with content the source doesn't contain.
- **NDMA documents from a state disaster-authority mirror:** only when the file is NDMA's own
  guideline, not a state rewrite. Then `source_name` is NDMA, `source_url` is the mirror URL, and
  `original_title` is NDMA's title.
- **Medical content:** no advice beyond what the source says.
- **Naming:** app text uses CPCB's 2014 band name "Moderately Polluted"; the document gives
  "Moderate" as an alias, so either word retrieves it.

## To add later (no usable official source yet)

- **IMD colour-coded warnings (green/yellow/orange/red):** the only official text found (an IMD
  bulletin on PIB) defines red only.
- **Dense fog and road safety:** no official IMD or MoRTH page found.
- **Heavy rain / urban flooding and travel safety:** NDMA's *Management of Urban Flooding* (2010)
  is aimed at authorities and has no personal safety advice.
- **Heat illness: signs and first aid.** NDMA's *Guidelines for Preparation of Action Plan -
  Prevention and Management of Heat Wave* (2019) is on ndma.gov.in, but the PDF has no text layer.
  Reading it needs a PDF renderer or OCR. NCDC's public heat material links return 404.
