# RapidOCR ONNX models (optional)

Place ONNX files here for fully offline OCR without first-run download:

- `rapidocr/ch_PP-OCRv4_det_infer.onnx`
- `rapidocr/ch_PP-OCRv4_rec_infer.onnx`
- `rapidocr/ch_ppocr_mobile_v2.0_cls_infer.onnx`

If missing, `rapidocr-onnxruntime` uses its package defaults when available.
On Windows, engine falls back to `scripts/ocr_frame.ps1` (Windows.Media.Ocr).

Speed stack: `onnxruntime-directml` (GPU via DirectML). Tip uses lighter preprocess
(no upscale); both tip+feed keep det+rec. Full CUDA EP needs `onnxruntime-gpu` + CUDA 13/cuDNN.
