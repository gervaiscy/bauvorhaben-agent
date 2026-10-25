The current flow is:

```text
PDF → OCR → Document boundaries
                  ├→ Contract facts
                  └→ Aufgaben → Offers → Timing
                       Contract facts ──────┘
```

| Step | Input | What happens | Output |
|---|---|---|---|
| **1. OCR** | PDF file | Render each page as an image. Gemini transcribes its visible text. The app assembles the result. | `pages.json`: page numbers and extracted text |
| **2. Document boundaries** | `pages.json` | Gemini identifies separate documents using five-page windows and previous-page context. | `documents.json`: document identities, titles and page ranges |
| **3. Contract facts** | Contract `pages.json` + `documents.json` | Extract agreements and obligations of both parties using three-page windows with one-page overlap. BLB is excluded. | `working-units.project-facts.json`, including quotations, source identities and review findings |
| **4. Aufgaben** | BLB or contract pages + document boundaries | Extract what must be done, who is responsible and who performs it. Uses three-page windows with one-page overlap, within document boundaries. | `working-units.json` and `working-units.units-only.json` |
| **5. Offers** | Saved Aufgaben output + original pages and boundaries | Extract optional/additional offers and their terms. Preserve existing tasks. | New cumulative `working-units.json` and `working-units.offers-only.json` |
| **6. Timing** | Saved Aufgaben-and-offers output + BLB pages/boundaries + contract facts + original contract pages/boundaries | Assess when each existing task must happen. | New cumulative `working-units.json` and `working-units.timing-only.json` |

For the **BLB timing phase**, these are the individual steps:

1. **Validate the inputs.**  
   Check that the saved tasks match the BLB text, document boundaries and window configuration. Verify the contract facts against their original sources.

2. **Select context for each task.**  
   Choose an existing three-page BLB window containing its cited pages, favoring following-page context. Windows never cross document boundaries.

3. **Build a timing request.**  
   Send Gemini:
   - Up to five existing tasks, including their citations.
   - The selected BLB window.
   - **All 72 contract facts**, labeled as project-wide context.
   - Supported date anchors, if supplied.

   The original contract files are used for source verification; the request receives the extracted facts and their quotations.

4. **Receive Gemini’s timing assessment.**  
   Per task, Gemini returns:
   - `status`
   - `timing_rules`: scope, expression, kind, event, relation, offset, duration, conditions and citations
   - `missing_evidence`

5. **Check the response.**  
   The app checks citations and incompatible or incomplete timing fields. Findings are recorded; Gemini’s response is preserved unchanged.

6. **Attempt calendar calculations.**  
   The app calculates dates only when a supported event date and usable timing rule exist. Otherwise, it records why the date remains unresolved. **Cross-source evidence handling in this calculator remains incomplete**, as identified in the review.

7. **Save separate artifacts.**

   | File | Contains |
   |---|---|
   | `working-units.json` | Existing tasks and offers, supplied facts, timing assessments and review findings |
   | `working-units.timing-only.json` | Gemini’s timing assessments only |
   | `working-units.gemini-raw.json` | Raw Gemini results from this timing run |
   | `working-units.trace/` | Exact prompts, responses and attempt evidence |

**Applicability and consequences have not run in this timing-only command.** The contract facts provide context; they do not yet determine whether every BLB task applies to this house.
