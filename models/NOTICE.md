# Bundled sentiment model attribution

The baseline container includes a quantized ONNX export of the DistilBERT model fine-tuned on English SST-2.

- Trained model publisher: [DistilBERT / Hugging Face](https://huggingface.co/distilbert/distilbert-base-uncased-finetuned-sst-2-english).
- ONNX export publisher: [Xenova](https://huggingface.co/Xenova/distilbert-base-uncased-finetuned-sst-2-english).
- Export revision: `0b6928efcb76139cae2c6881d49cda67fe119f42`.
- Quantization/export is an upstream transformation; this project does not modify the downloaded weights.
- The trained-model card declares Apache-2.0. The export card links to that model and does not declare a separate license. Retain the publishers' attribution and verify any additional upstream notices before redistribution.
- [Apache License 2.0 copy](LICENSE-APACHE-2.0.txt), sourced verbatim from [the Apache Software Foundation](https://www.apache.org/licenses/LICENSE-2.0.txt).

This notice concerns the bundled model only; it does not assign a new license to this project's application code or replace dependency/upstream notices. Optional Qwen/Kitten/Groq artifacts are not bundled in the baseline image.
