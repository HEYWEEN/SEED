import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from .jobs import JobError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL = "laptop"
ASPECT_RULE = 2
MAX_SENTENCES = 160


_RUNNER_REVISION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def pipeline_folder():
    return Path(os.environ.get(
        "SEED_SESAME_PIPELINE", PROJECT_ROOT.parent.parent / "SESAME" / "pipeline"
    )).expanduser().resolve()


def pipeline_python(pipeline):
    return Path(os.environ.get(
        "SEED_SESAME_PYTHON", pipeline / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )).expanduser().absolute()


def cache_signature():
    pipeline = pipeline_folder()
    engine = pipeline / "SESAME"
    files = [
        pipeline_python(pipeline),
        pipeline / "config.py",
        pipeline / "reproducibility.py",
        *(engine / name for name in (
            "EASTER_en.py", "extract_opinion_en.py", "extract_aspect.py",
            f"data/pretrained/{MODEL}_model.h5",
            "data/dict/SentiWordNet_3.0.0.txt",
            "data/pred_aspect/SentiAspectExtractor.jar",
        )),
    ]
    snapshot = None
    try:
        snapshot = _roberta_snapshot()
    except JobError:
        pass
    if snapshot:
        files.extend(Path(snapshot) / name for name in ("config.json", "tokenizer.json", "vocab.json", "merges.txt"))

    stamps = []
    for file in files:
        try:
            info = file.stat()
            stamps.append([str(file), info.st_size, info.st_mtime_ns])
        except FileNotFoundError:
            stamps.append([str(file), None])
    return [MODEL, ASPECT_RULE, _RUNNER_REVISION, snapshot, os.environ.get("SESAME_SEED", "42"), stamps]


def run_sesame(discussion, report):
    pipeline = pipeline_folder()
    python = pipeline_python(pipeline)
    if not (pipeline / "SESAME/EASTER_en.py").is_file() or not python.is_file():
        raise JobError("sesame_runtime_missing")
    if not (pipeline / "SESAME/data/pretrained" / f"{MODEL}_model.h5").is_file():
        raise JobError("sesame_model_missing")

    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "TF_CPP_MIN_LOG_LEVEL": "2",
        "TF_NUM_INTRAOP_THREADS": "2",
        "TF_NUM_INTEROP_THREADS": "2",
    }
    env.pop("SESAME_CORENLP_SPAWN", None)
    # The SESAME subprocess needs only local models and the public runtime configuration.
    env.pop("DEEPSEEK_API_KEY", None)

    with TemporaryDirectory(prefix="seed-sesame-") as temporary:
        directory = Path(temporary)
        (directory / "input.json").write_text(json.dumps(discussion), encoding="utf-8")
        progress_path = directory / "progress"
        result_path = directory / "result.json"
        report("split")
        try:
            process = subprocess.Popen(
                [str(python), "-m", "backend.sesame", str(pipeline), temporary],
                cwd=PROJECT_ROOT, env=env, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=os.name == "posix",
            )
        except OSError:
            raise JobError("sesame_runtime_missing") from None
        deadline = monotonic() + 1200
        while True:
            try:
                process.wait(timeout=0.7)
                break
            except subprocess.TimeoutExpired:
                if monotonic() >= deadline:
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        process.kill()
                    process.wait()
                    raise JobError("sesame_timeout") from None
                if progress_path.is_file():
                    stage = progress_path.read_text(encoding="utf-8")
                    if stage:
                        report(stage)

        if not result_path.is_file():
            raise JobError("sesame_failed")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if "error" in result:
            raise JobError(result["error"])
        if process.returncode != 0:
            raise JobError("sesame_failed")
        return result


def _roberta_snapshot():
    configured = os.environ.get("SESAME_ROBERTA_PATH")
    if configured:
        return configured
    home = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface"))
    root = home / "hub/models--roberta-base"
    reference = root / "refs/main"
    if reference.is_file():
        candidate = root / "snapshots" / reference.read_text(encoding="utf-8").strip()
        if (candidate / "config.json").is_file():
            return str(candidate)
    raise JobError("sesame_model_missing")


def _sentence_spans(text, core):
    response = json.loads(core.annotate(text, properties={
        "annotators": "tokenize,ssplit",
        "outputFormat": "json",
        "ssplit.newlineIsSentenceBreak": "two",
        "timeout": 30000,
    }))
    utf16 = text.encode("utf-16-le")
    for sentence in response["sentences"]:
        tokens = sentence["tokens"]
        if tokens:
            start = tokens[0]["characterOffsetBegin"]
            end = tokens[-1]["characterOffsetEnd"]
            yield start, end, utf16[2 * start:2 * end].decode("utf-16-le")


def _phrase(text, sentence_start, indices):
    if len(indices) != 2:
        return None
    left, right = indices
    words = list(re.finditer(r"\S+", text))
    if not 0 <= left < right <= len(words):
        return None
    start, end = words[left].start(), words[right - 1].end()
    return {
        "text": text[start:end],
        "span_utf16": [
            sentence_start + len(text[:start].encode("utf-16-le")) // 2,
            sentence_start + len(text[:end].encode("utf-16-le")) // 2,
        ],
    }


def _predictor(model, tokenizer):
    import numpy as np
    import tensorflow as tf

    @tf.function(input_signature=[tf.TensorSpec([None, None], tf.int32)] * 2)
    def predict_batch(input_ids, attention_mask):
        return model({"input_ids": input_ids, "attention_mask": attention_mask}, training=False)

    def predict(texts):
        encoded = tokenizer([str(text) for text in texts], truncation=True, max_length=512)
        sizes = {}
        for index, ids in enumerate(encoded["input_ids"]):
            sizes.setdefault(len(ids), []).append(index)

        output = np.empty((len(texts), 3), dtype=np.float32)
        # Group same-length inputs so the TextCNN sees the same unpadded inputs as DataGenerator.
        for indices in sizes.values():
            ids = tf.constant([encoded["input_ids"][i] for i in indices], dtype=tf.int32)
            masks = tf.constant([encoded["attention_mask"][i] for i in indices], dtype=tf.int32)
            output[indices] = predict_batch(ids, masks).numpy()
        return output

    return predict


def _execute(pipeline, directory, report):
    try:
        with socket.create_connection(("127.0.0.1", 9000), timeout=3):
            pass
    except OSError:
        raise JobError("corenlp_unavailable") from None

    os.environ["SESAME_ROBERTA_PATH"] = _roberta_snapshot()
    sys.path[:0] = [str(pipeline / "SESAME"), str(pipeline)]
    from stanfordcorenlp import StanfordCoreNLP

    discussion = json.loads((directory / "input.json").read_text(encoding="utf-8"))
    core = StanfordCoreNLP("http://127.0.0.1", port=9000, timeout=30000)
    sentences = [
        (message["message_id"], start, end, text)
        for message in discussion["messages"]
        for start, end, text in _sentence_spans(message["text"], core)
    ]
    if len(sentences) > MAX_SENTENCES:
        raise JobError("too_many_sentences")
    result = {
        "message_count": len(discussion["messages"]),
        "sentence_count": len(sentences),
        "candidate_count": 0,
        "engine": {"model": MODEL, "aspect_rule": ASPECT_RULE},
        "evaluations": [],
    }
    if not sentences:
        return result

    report("classify")
    import numpy as np
    from reproducibility import set_seeds
    import EASTER_en
    from shap import Explainer
    from shap.maskers import Text

    set_seeds()
    model = EASTER_en.load_model(f"{MODEL}_model", *EASTER_en.model_params[MODEL], True)
    tokenizer = EASTER_en.DataGenerator.tokenizer
    predict = _predictor(model, tokenizer)
    texts = [" ".join(sentence[3].split()) for sentence in sentences]
    probabilities = np.concatenate([predict(texts[i:i + 12]) for i in range(0, len(texts), 12)])
    labels = probabilities.argmax(axis=1) - 1
    candidates = [i for i, label in enumerate(labels) if label != 0]
    result["candidate_count"] = len(candidates)
    if not candidates:
        return result

    report("shap")
    shap_path = directory / "shap.jsonl"
    explain = Explainer(predict, Text(tokenizer))
    with shap_path.open("w", encoding="utf-8") as output:
        for position, index in enumerate(candidates, 1):
            report(f"shap:{position}/{len(candidates)}")
            evidence = explain([texts[index]], silent=True)
            output.write(json.dumps({
                "text": texts[index],
                "senti": int(labels[index]),
                "senti_prob": probabilities[index].tolist(),
                "word_piece": evidence.data[0][1:-1].tolist(),
                "shaps_arr": evidence.values[0].tolist(),
            }) + "\n")

    report("opinion")
    from extract_opinion_en import OpinionExtractor, std_p_value
    from extract_aspect import extract_aspect, is_sentimantal_aspect

    extractor = OpinionExtractor(str(shap_path))
    extractor.is_consider_multi_sentiment_through_adversative = True
    opinions = extractor.extract_clause_opinion(std_p=std_p_value)
    if len(opinions) != len(candidates):
        raise JobError("sesame_failed")
    report("aspect")
    linked = extract_aspect(
        MODEL, "en", [texts[i] for i in candidates],
        [[op["actual_index"] for op in row] for row in opinions],
        [[op["is_potential_aspect"] for op in row] for row in opinions],
        core_extend_type=ASPECT_RULE - 1,
    )
    if len(linked) != len(candidates):
        raise JobError("sesame_failed")

    for index, opinion_row, aspect_row in zip(candidates, opinions, linked):
        message_id, start, end, text = sentences[index]
        seen = set()
        for opinion in opinion_row:
            sentiment = opinion["sentiment"]
            indices = opinion["actual_index"]
            phrase = _phrase(text, start, indices)
            if sentiment not in (-1, 1) or not phrase or opinion["shap_value"] <= 0:
                continue
            compatible = []
            for pair in aspect_row:
                if pair.get("opinion") != indices:
                    continue
                aspect = _phrase(text, start, pair["aspect"])
                if aspect and not is_sentimantal_aspect("en", pair["aspect"], texts[index]):
                    compatible.append(aspect)
            for aspect in compatible or [None]:
                signature = sentiment, tuple(phrase["span_utf16"]), tuple(aspect["span_utf16"]) if aspect else ()
                if signature in seen:
                    continue
                seen.add(signature)
                result["evaluations"].append({
                    "evaluation_id": f"E{len(result['evaluations']) + 1}",
                    "source_id": f"S{index + 1}",
                    "message_id": message_id,
                    "sentence": text,
                    "sentence_span_utf16": [start, end],
                    "sentiment": "positive" if sentiment == 1 else "negative",
                    "confidence": float(probabilities[index, sentiment + 1]),
                    "opinion": phrase,
                    "aspect": aspect,
                })
    return result


def main():
    pipeline, directory = map(Path, sys.argv[1:])
    progress_path = directory / "progress"

    def report(stage):
        pending = progress_path.with_suffix(".next")
        pending.write_text(stage, encoding="utf-8")
        pending.replace(progress_path)

    try:
        result = _execute(pipeline, directory, report)
    except JobError as error:
        result = {"error": error.code}
    (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
