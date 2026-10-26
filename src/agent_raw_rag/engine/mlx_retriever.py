"""Apple Silicon MLX Two-Stage Retrieval Engine.

Implements Architectural Invariants #4 & #5:
- Two-Stage Bounded Retrieval: Top-128 Dense Cosine -> Top-15 Cross-Encoder Reranker.
- Chronological Reordering: Ascending order by (session_timestamp, start_turn, start_token).
- Display-Time Dynamic Turn Expansion: complete conversational context with turn-level deduplication.
- Hardware-optimized for 64GB Apple Silicon via MLX 4-bit weights and 2-column projection head.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np

from ..config import RetrievalConfig
from ..models import ChunkRecord, SearchResult, TurnContext
from .chunker import IncrementalChunker
from .store import SQLiteStore

# Try importing MLX packages
try:
    import mlx.core as mx
    from mlx_lm import load
    HAS_MLX = True
except ImportError:
    HAS_MLX = False


class MLXRetriever:
    def __init__(
        self,
        config: RetrievalConfig,
        store: SQLiteStore,
        chunker: Optional[IncrementalChunker] = None,
    ):
        self.config = config
        self.store = store
        self.chunker = chunker or IncrementalChunker()

        self._embed_model_bundle: Optional[Dict[str, Any]] = None
        self._rerank_model_bundle: Optional[Dict[str, Any]] = None
        self._embed_failed: bool = False
        self._rerank_failed: bool = False
        self._embed_cache: Dict[str, np.ndarray] = {}

    @property
    def engine_status(self) -> Dict[str, Any]:
        """Expose current inference engine and model backend details."""
        is_fallback = (not HAS_MLX) or self._embed_failed or self._rerank_failed

        embed_status = (
            "mlx_native"
            if (HAS_MLX and self._embed_model_bundle is not None)
            else ("fallback_hash" if (not HAS_MLX or self._embed_failed) else "standby")
        )
        rerank_status = (
            "mlx_native"
            if (HAS_MLX and self._rerank_model_bundle is not None)
            else ("fallback_token_overlap" if (not HAS_MLX or self._rerank_failed) else "standby")
        )

        return {
            "has_mlx": HAS_MLX,
            "is_fallback": is_fallback,
            "mode": "FALLBACK" if is_fallback else "MLX_ACCELERATED",
            "device": "Apple Silicon (Metal GPU)" if HAS_MLX else "CPU",
            "embedding": {
                "model": self.config.embedding_model,
                "dimension": 2560,
                "status": embed_status,
                "loaded": self._embed_model_bundle is not None,
            },
            "reranker": {
                "model": self.config.reranker_model,
                "status": rerank_status,
                "loaded": self._rerank_model_bundle is not None,
                "dynamic_top_k": {
                    "enabled": self.config.dynamic_top_k.enabled,
                    "score_threshold": self.config.dynamic_top_k.score_threshold,
                    "fallback_top_k": self.config.dynamic_top_k.fallback_top_k,
                },
            },
        }

    def _load_embed_model(self) -> Optional[Dict[str, Any]]:
        if not HAS_MLX:
            return None
        if self._embed_model_bundle is not None:
            return self._embed_model_bundle

        repo_id = self.config.embedding_model
        try:
            model, tokenizer = load(repo_id)
            pad_id = (
                tokenizer.pad_token_id
                if tokenizer.pad_token_id is not None
                else (tokenizer.eos_token_id or 151643)
            )
            self._embed_model_bundle = {
                "model": model,
                "tokenizer": tokenizer,
                "pad_id": pad_id,
            }
            return self._embed_model_bundle
        except Exception as e:
            self._embed_failed = True
            print(f"[MLXRetriever] Warning: Could not load embed model {repo_id}: {e}")
            return None

    def _load_rerank_model(self) -> Optional[Dict[str, Any]]:
        if not HAS_MLX:
            return None
        if self._rerank_model_bundle is not None:
            return self._rerank_model_bundle

        repo_id = self.config.reranker_model
        try:
            model, tokenizer = load(repo_id)
            yes_id = tokenizer.encode("yes", add_special_tokens=False)[0]
            no_id = tokenizer.encode("no", add_special_tokens=False)[0]
            pad_id = (
                tokenizer.pad_token_id
                if tokenizer.pad_token_id is not None
                else (tokenizer.eos_token_id or 151643)
            )
            d = model.args.hidden_size
            eye = mx.eye(d, dtype=mx.float16)
            full_w = (
                model.lm_head(eye)
                if hasattr(model, "lm_head")
                else model.model.embed_tokens.as_linear(eye)
            )
            w_no_yes = full_w[:, [no_id, yes_id]]
            mx.eval(w_no_yes)

            self._rerank_model_bundle = {
                "model": model,
                "tokenizer": tokenizer,
                "yes_id": yes_id,
                "no_id": no_id,
                "pad_id": pad_id,
                "w_no_yes": w_no_yes,
            }
            return self._rerank_model_bundle
        except Exception as e:
            self._rerank_failed = True
            print(f"[MLXRetriever] Warning: Could not load rerank model {repo_id}: {e}")
            return None

    def embed_texts(
        self,
        texts: List[str],
        is_query: bool = False,
        batch_size: int = 16,
        max_length: int = 4096,
    ) -> List[np.ndarray]:
        """Compute L2-normalized embeddings via MLX Qwen3-Embedding or fallback."""
        if not texts:
            return []

        bundle = self._load_embed_model()
        if bundle is None:
            # Deterministic hash-based embedding fallback for testing environments without models
            out = []
            for t in texts:
                vec = np.zeros(2560, dtype=np.float32)
                for w in t.split():
                    h = hash(w) % 2560
                    vec[h] += 1.0
                norm = np.linalg.norm(vec)
                if norm > 1e-6:
                    vec /= norm
                out.append(vec)
            return out

        model = bundle["model"]
        tokenizer = bundle["tokenizer"]
        pad_id = bundle["pad_id"]

        results: List[Optional[np.ndarray]] = [None] * len(texts)
        missing_indices: List[int] = []
        encoded: List[List[int]] = []

        for i, text in enumerate(texts):
            clean_text = text if text else " "
            if is_query:
                clean_text = f"Instruct: Given a search query, retrieve relevant conversation passages that answer the query\nQuery: {clean_text}"

            ckey = clean_text
            if ckey in self._embed_cache:
                results[i] = self._embed_cache[ckey]
            else:
                missing_indices.append(i)
                ids = tokenizer.encode(clean_text)
                if len(ids) > max_length:
                    ids = ids[: max_length - 1] + [ids[-1]]
                encoded.append(ids)

        if missing_indices:
            order = sorted(range(len(missing_indices)), key=lambda k: len(encoded[k]))
            for start in range(0, len(order), batch_size):
                batch_pos = order[start : start + batch_size]
                batch_seqs = [encoded[k] for k in batch_pos]
                lengths = [len(seq) for seq in batch_seqs]
                max_len = max(lengths)
                padded = [seq + [pad_id] * (max_len - len(seq)) for seq in batch_seqs]

                input_ids = mx.array(padded, dtype=mx.int32)
                hidden = model.model(input_ids)
                last_pos = mx.array([l - 1 for l in lengths], dtype=mx.int32)
                pooled = hidden[mx.arange(len(batch_seqs)), last_pos, :]
                norms = mx.linalg.norm(pooled, axis=-1, keepdims=True)
                normalized = pooled / mx.maximum(norms, 1e-12)
                mx.eval(normalized)
                np_vecs = np.asarray(normalized.astype(mx.float32))

                for local_idx, k in enumerate(batch_pos):
                    orig_idx = missing_indices[k]
                    vec = np_vecs[local_idx]
                    results[orig_idx] = vec
                    if len(self._embed_cache) < 20000:
                        self._embed_cache[clean_text] = vec
                mx.clear_cache()

        return results  # type: ignore[return-value]

    def compute_rerank_scores(
        self,
        query: str,
        documents: List[str],
        batch_size: int = 16,
        max_length: int = 2048,
    ) -> List[float]:
        """Compute reranking scores for candidate documents via Qwen3-Reranker."""
        if not documents:
            return []

        bundle = self._load_rerank_model()
        if bundle is None:
            # Simple fallback token overlap scoring
            q_tokens = set(query.lower().split())
            scores = []
            for doc in documents:
                d_tokens = set(doc.lower().split())
                overlap = len(q_tokens.intersection(d_tokens))
                scores.append(float(overlap) / (len(q_tokens) + 1e-5))
            return scores

        model = bundle["model"]
        tokenizer = bundle["tokenizer"]
        pad_id = bundle["pad_id"]
        w_no_yes = bundle["w_no_yes"]

        prefix = (
            "<|im_start|>system\n"
            "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
            'Note that the answer can only be "yes" or "no".<|im_end|>\n'
            "<|im_start|>user\n"
        )
        instruction = (
            "Given a question and a passage, determine if the passage contains "
            "information relevant to answering the question."
        )
        formatted_query = f"{prefix}<Instruct>: {instruction}\n<Query>: {query}\n"
        suffix_str = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

        q_ids = tokenizer.encode(formatted_query, add_special_tokens=False)
        suffix_ids = tokenizer.encode(suffix_str, add_special_tokens=False)

        encoded_pairs: List[List[int]] = []
        for doc in documents:
            doc_str = f"<Document>: {doc}{suffix_str}"
            d_ids = tokenizer.encode(doc_str, add_special_tokens=False)
            full_ids = q_ids + d_ids
            if len(full_ids) > max_length:
                full_ids = full_ids[: max_length - len(suffix_ids)] + suffix_ids
            encoded_pairs.append(full_ids)

        scores: List[float] = [0.0] * len(documents)
        order = sorted(range(len(documents)), key=lambda i: len(encoded_pairs[i]))

        for start in range(0, len(order), batch_size):
            batch_indices = order[start : start + batch_size]
            batch_seqs = [encoded_pairs[i] for i in batch_indices]
            lengths = [len(seq) for seq in batch_seqs]
            max_len = max(lengths)
            padded = [seq + [pad_id] * (max_len - len(seq)) for seq in batch_seqs]

            input_ids = mx.array(padded, dtype=mx.int32)
            hidden = model.model(input_ids)
            last_pos = mx.array([l - 1 for l in lengths], dtype=mx.int32)
            last_h = hidden[mx.arange(len(batch_seqs)), last_pos, :]
            pair_logits = last_h.astype(mx.float16) @ w_no_yes
            probs = mx.softmax(pair_logits.astype(mx.float32), axis=-1)[:, 1]
            mx.eval(probs)
            prob_list = probs.tolist()
            for local_idx, orig_idx in enumerate(batch_indices):
                scores[orig_idx] = float(prob_list[local_idx])
            mx.clear_cache()

        return scores

    def search(
        self,
        query: str,
        project_path: Optional[str] = None,
        agent: Optional[str] = None,
        after_date: Optional[str] = None,
        before_date: Optional[str] = None,
        top_k: int = 15,
    ) -> List[SearchResult]:
        """Execute the two-stage Raw-RAG bounded search pipeline."""
        t0 = time.perf_counter()

        # Step 1: Pre-filter candidate vector index (Deferred Loading)
        chunk_ids, matrix = self.store.load_candidate_vector_index(
            project_path=project_path,
            agent=agent,
            after_date=after_date,
            before_date=before_date,
        )
        if not chunk_ids:
            return []

        # Step 2: Dense Cosine Similarity (Top-128)
        query_vec = self.embed_texts([query], is_query=True)[0]
        # Cosine similarity since embeddings are normalized
        sim_scores = np.dot(matrix, query_vec)

        candidate_cap = min(self.config.candidate_top_k, len(chunk_ids))
        top_cand_indices = np.argsort(sim_scores)[::-1][:candidate_cap]

        top_chunk_ids = [chunk_ids[i] for i in top_cand_indices]
        top_sims = [float(sim_scores[i]) for i in top_cand_indices]

        # Step 2.5: Hydrate only Top-K candidates for Reranking
        chunks_map = self.store.get_chunks_by_ids(top_chunk_ids)
        cand_chunks: List[ChunkRecord] = []
        cand_sims: List[float] = []
        for cid, sim in zip(top_chunk_ids, top_sims):
            if cid in chunks_map:
                cand_chunks.append(chunks_map[cid])
                cand_sims.append(sim)

        if not cand_chunks:
            return []

        # Step 3: Cross-Encoder Rerank (Top-15)
        rerank_scores = self.compute_rerank_scores(
            query=query,
            documents=[c.content for c in cand_chunks],
        )

        # Pair candidates with scores
        scored_candidates = []
        for chunk, sim, r_score in zip(cand_chunks, cand_sims, rerank_scores):
            scored_candidates.append((chunk, sim, r_score))

        # Sort by rerank score descending
        scored_candidates.sort(key=lambda x: x[2], reverse=True)

        # Dynamic Top-k or Fixed Top-k
        final_top_k = min(top_k or self.config.rerank_top_k, len(scored_candidates))
        selected = scored_candidates[:final_top_k]

        if self.config.dynamic_top_k.enabled:
            threshold = self.config.dynamic_top_k.score_threshold
            filtered = [item for item in selected if item[2] >= threshold]
            if len(filtered) < self.config.dynamic_top_k.fallback_top_k:
                selected = scored_candidates[: self.config.dynamic_top_k.fallback_top_k]
            else:
                selected = filtered

        # Step 4: Chronological Reordering (Ascending)
        if self.config.chronological_reorder:
            selected.sort(
                key=lambda x: (
                    x[0].session_timestamp or "",
                    x[0].start_turn,
                    x[0].start_token,
                    x[0].chunk_index,
                )
            )

        # Step 5: Display-Time Dynamic Turn Expansion (Batch Fetch Contexts - 0 N+1 Queries)
        results: List[SearchResult] = []
        seen_turns: Set[Tuple[str, int]] = set()

        target_session_ids = {chunk.session_id for chunk, _, _ in selected}
        contexts_map = self.store.get_turn_contexts_batch(target_session_ids)

        for chunk, sim, r_score in selected:
            turn_ctx = contexts_map.get(chunk.session_id)
            if turn_ctx:
                expanded_text = self.chunker.expand_chunk_to_turns(
                    chunk, turn_ctx, seen_turns=seen_turns
                )
            else:
                expanded_text = chunk.content

            results.append(
                SearchResult(
                    chunk_id=chunk.chunk_id,
                    session_id=chunk.session_id,
                    chunk_index=chunk.chunk_index,
                    agent=chunk.agent,
                    project_path=chunk.project_path,
                    similarity_score=sim,
                    rerank_score=r_score,
                    start_date=chunk.start_date,
                    raw_content=chunk.content,
                    expanded_turn_content=expanded_text,
                    start_turn=chunk.start_turn,
                    end_turn=chunk.end_turn,
                )
            )

        return results
