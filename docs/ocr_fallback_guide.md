# OCR Fallback for PDF Extraction

## Overview

The medical pipeline's PDF extraction now includes **automatic OCR fallback** for handling scanned PDFs or documents with poor text layers.

### How It Works

1. **Primary**: PyMuPDF text layer extraction (fast, high quality)
2. **Quality Check**: If a page has fewer than 50 words (configurable), trigger OCR
3. **Fallback**: Tesseract OCR with German language model
4. **Graceful Degradation**: If OCR unavailable, use text layer despite low quality

## Installation

### Python Dependencies

Already installed with the medical pipeline:
```bash
pip install PyMuPDF pillow pytesseract
```

### Tesseract OCR Binary

**Windows**:
1. Download installer: https://github.com/UB-Mannheim/tesseract/wiki
2. Install to default location (e.g., `C:\Program Files\Tesseract-OCR\`)
3. Add to PATH or pytesseract will find it automatically

**Linux**:
```bash
sudo apt-get install tesseract-ocr tesseract-ocr-deu
```

**macOS**:
```bash
brew install tesseract tesseract-lang
```

### Verify Installation

```bash
python -c "import pytesseract; print(pytesseract.get_tesseract_version())"
```

Expected output: `tesseract 5.x.x`

## Usage

### Automatic (Pipeline Integration)

OCR fallback is **automatically enabled** in the medical pipeline:

```bash
python generic_main.py --config configs/pipeline_medical_single.json --hash <doi_hash>
```

When the PDF conversion step runs:
- Pages with good text layers → fast PyMuPDF extraction
- Pages with poor/missing text → automatic OCR fallback
- You'll see log messages like:
  ```
  [WARN] Page 2: Only 15 words extracted, using OCR fallback...
  [OK] Page 2: OCR extracted 456 words
  ```

### Manual (Standalone Script)

```bash
# Basic usage
python scripts/advanced_pdf_conversion.py/basic_segmentation.py \
    "data/<hash>/<hash>.pdf" -o output.md

# Pipeline integration
python scripts/advanced_pdf_conversion.py/basic_segmentation.py \
    --doi-hash <hash> --pipeline-override

# Disable OCR fallback
python scripts/advanced_pdf_conversion.py/basic_segmentation.py \
    "data/<hash>/<hash>.pdf" -o output.md --no-ocr

# Adjust OCR threshold (default: 50 words/page)
python scripts/advanced_pdf_conversion.py/basic_segmentation.py \
    "data/<hash>/<hash>.pdf" -o output.md --min-words 100
```

## Configuration

### OCR Threshold

Controls when to trigger OCR fallback:

```bash
--min-words 50  # Default: OCR if page has < 50 words
```

- **Lower threshold** (e.g., 20): More aggressive, OCR even for partially good text
- **Higher threshold** (e.g., 100): Conservative, OCR only for very poor text

### Language Models

Default: German (`deu`)
Fallback: English (`eng`)

To use different language models, install additional Tesseract language packs:

**Windows**: Select during Tesseract installer
**Linux**: `sudo apt-get install tesseract-ocr-<lang>`
**macOS**: Included with `tesseract-lang`

## Performance

### Speed Comparison

| Method | Speed | Quality |
|--------|-------|---------|
| PyMuPDF text layer | ~0.5s per page | High (if text layer good) |
| Tesseract OCR @ 300 DPI | ~2-5s per page | Good |
| Tesseract OCR @ 200 DPI | ~1-3s per page | Acceptable |

### Optimization Tips

1. **Text-layer PDFs**: No performance impact (OCR not triggered)
2. **Mixed PDFs**: Only slow pages use OCR
3. **Full scanned PDFs**: Expect 2-5 seconds per page
4. **Lower DPI** for faster OCR: Modify `dpi=300` → `dpi=200` in `extract_words_ocr()`

## Troubleshooting

### "Tesseract not found"

**Solution**:
1. Install Tesseract binary (see Installation section)
2. On Windows, ensure it's in PATH or at default location
3. Verify: `tesseract --version`

### "German language model not found"

**Error**: `Error opening data file \Tesseract-OCR\tessdata/deu.traineddata`

**Solution**:
1. Windows: Re-run Tesseract installer, select "Additional language data" → German
2. Linux: `sudo apt-get install tesseract-ocr-deu`
3. macOS: Already included with `tesseract-lang`

**Workaround**: Script automatically falls back to English if German fails

### OCR Output Quality Issues

If OCR produces poor results:

1. **Check image quality**: `page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))`
   - Increase DPI: 300 → 400 (slower but better quality)
2. **Check page rotation**: Some PDFs have rotated content
3. **Pre-process image**: Add contrast enhancement, noise reduction
4. **Verify confidence**: Lower confidence threshold from 30 if too aggressive

## Testing

### Test OCR Fallback

Create a low-quality test case:

```bash
# Force OCR on a good PDF by setting high threshold
python scripts/advanced_pdf_conversion.py/basic_segmentation.py \
    "data/ec5d5219/ec5d5219.pdf" -o test_ocr.md --min-words 10000
```

Expected output:
```
[WARN] Page 1: Only 3928 words extracted, using OCR fallback...
[OK] Page 1: OCR extracted 4156 words
```

### Benchmark OCR vs Text Layer

```python
import time
from pathlib import Path

pdf_path = "data/ec5d5219/ec5d5219.pdf"

# Time text layer
start = time.time()
md_text = convert_pdf_to_markdown(pdf_path, use_ocr_fallback=False)
print(f"Text layer: {time.time() - start:.2f}s")

# Time with OCR forced
start = time.time()
md_ocr = convert_pdf_to_markdown(pdf_path, min_words_threshold=10000)
print(f"With OCR: {time.time() - start:.2f}s")
```

## Integration with Pipeline

The OCR fallback is already integrated into `src/pipelines/pdf_conversion/convert.py`:

```python
# Medical pipeline automatically uses basic_segmentation.py with OCR fallback
if _is_medical_pipeline(config):
    print(f"    [MEDICAL MODE] Using layout-aware extraction")
    bs = _load_basic_segmentation_module()
    md_content = bs.convert_pdf_to_markdown(pdf_path)  # OCR fallback enabled by default
```

No configuration changes needed - it just works!

## Advanced: Alternative Approaches

### Option 1: pdfplumber-based OCR Fallback

Created: `scripts/advanced_pdf_conversion.py/ocr_fallback_segmentation.py`

Uses pdfplumber as primary, OCR as fallback. **Note**: Some PDFs have character-level extraction issues with pdfplumber (see ec5d5219 example).

```bash
python scripts/advanced_pdf_conversion.py/ocr_fallback_segmentation.py \
    --doi-hash <hash> --pipeline-override
```

### Option 2: docling with OCR

For complex layouts with tables, consider docling (already in pipeline for table extraction):

```python
from docling.document_converter import DocumentConverter
converter = DocumentConverter()
result = converter.convert(pdf_path)
```

## See Also

- [Medical Pipeline PDF Extraction Integration](./medical_pdf_extraction_integration.md)
- [basic_segmentation.py](../scripts/advanced_pdf_conversion.py/basic_segmentation.py) - Main implementation
- [ocr_fallback_segmentation.py](../scripts/advanced_pdf_conversion.py/ocr_fallback_segmentation.py) - pdfplumber variant
