---
name: documents
description: Turn an answer into a file — choose the format, write it, edit it later and roll a change back. An upload is never edited in place.
allowed-tools: write_document, edit_document, restore_document_version
---
# Documents

1. Write a file only when the user asked for one ("save", "as a file", "make a report/table")
   or when the answer is a table they will keep. An ordinary answer stays in chat.
2. Choose the format by what the file is for:
   - `.md` for prose and notes, `.txt` for plain text;
   - `.csv` for a simple table, `.xlsx` for several sheets or when someone will edit cells;
   - `.json` only when the content is structured data that must parse;
   - `.docx` when someone will edit it in Word, `.pdf` when the layout must not move.
3. Pass table data as `rows` (a list of rows, each a list of cells), never as one long string
   with commas. Use `sheets` for a multi-sheet workbook.
4. A document this assistant wrote can be changed with `edit_document` and rolled back with
   `restore_document_version`; pass the whole new body, not a diff. Such a file is not indexed
   automatically — mention the index switch on its card.
5. An uploaded document cannot be edited in place: its bytes may be shared. Build a new
   document from it with `write_document` under a new name, and edit that one.
6. Appending works only for text formats; a `.docx`, `.xlsx` or `.pdf` is rewritten, not grown.
7. Name the file after what it contains. Never try to overwrite an existing name — the server
   adds ` (2)` itself.
