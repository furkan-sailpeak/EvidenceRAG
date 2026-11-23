# data/

| Folder | What | Made by |
|---|---|---|
| `pdfs/` | **Put your documents here**: PDFs, or scans as PNG/JPG/TIFF. The file name (without extension) is the document's ID. | you |
| `ocr/` | One JSON per document: the text and metadata of every page. | `make ocr` |
| `backups/` | Earlier OCR results (`make ocr FORCE=1`) and full backups (`make backup`). | make |
| `models/` | The local embedding model, downloaded on first use. | `make embed` |

Everything except `pdfs/` can be rebuilt with `make prepare`. None of it is committed to git.
