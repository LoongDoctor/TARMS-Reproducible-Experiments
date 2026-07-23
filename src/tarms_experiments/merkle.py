"""Tagged binary Merkle tree with explicit membership proofs."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Iterable


LEAF_TAG = b"TARMS-LEAF-v1\x00"
NODE_TAG = b"TARMS-NODE-v1\x00"
ROOT_TAG = b"TARMS-ROOT-v1\x00"


def _leaf_hash(payload: bytes) -> bytes:
    return hashlib.sha256(LEAF_TAG + payload).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(NODE_TAG + left + right).digest()


def _root_hash(leaf_count: int, top_hash: bytes) -> bytes:
    if leaf_count <= 0 or leaf_count >= 2**64 or len(top_hash) != 32:
        raise ValueError("invalid Merkle root inputs")
    return hashlib.sha256(
        ROOT_TAG + leaf_count.to_bytes(8, "big") + top_hash
    ).digest()


@dataclass(frozen=True)
class ProofStep:
    side: str
    sibling: bytes


class MerkleTree:
    def __init__(self, payloads: Iterable[bytes]):
        material = [bytes(payload) for payload in payloads]
        if not material:
            raise ValueError("Merkle tree requires at least one payload")
        self._leaf_count = len(material)
        self._layers: list[list[bytes]] = [[_leaf_hash(payload) for payload in material]]
        while len(self._layers[-1]) > 1:
            current = self._layers[-1]
            parent: list[bytes] = []
            for index in range(0, len(current), 2):
                left = current[index]
                right = current[index + 1] if index + 1 < len(current) else left
                parent.append(_node_hash(left, right))
            self._layers.append(parent)

    @property
    def root(self) -> bytes:
        return _root_hash(self._leaf_count, self._layers[-1][0])

    def proof(self, leaf_index: int) -> list[ProofStep]:
        if leaf_index < 0 or leaf_index >= len(self._layers[0]):
            raise IndexError("leaf index out of range")
        proof: list[ProofStep] = []
        index = leaf_index
        for layer in self._layers[:-1]:
            if index % 2 == 0:
                sibling_index = index + 1 if index + 1 < len(layer) else index
                proof.append(ProofStep("right", layer[sibling_index]))
            else:
                proof.append(ProofStep("left", layer[index - 1]))
            index //= 2
        return proof


def verify_proof(
    payload: bytes,
    leaf_index: int,
    leaf_count: int,
    proof: Iterable[ProofStep],
    expected_root: bytes,
) -> bool:
    if (
        isinstance(leaf_index, bool)
        or not isinstance(leaf_index, int)
        or isinstance(leaf_count, bool)
        or not isinstance(leaf_count, int)
        or leaf_count <= 0
        or leaf_count >= 2**64
        or leaf_index < 0
        or leaf_index >= leaf_count
    ):
        return False
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        return False
    if not isinstance(expected_root, (bytes, bytearray, memoryview)):
        return False
    root = bytes(expected_root)
    if len(root) != 32:
        return False
    try:
        steps = list(proof)
    except TypeError:
        return False
    expected_depth = (leaf_count - 1).bit_length()
    if len(steps) != expected_depth:
        return False
    value = _leaf_hash(bytes(payload))
    index = leaf_index
    width = leaf_count
    for step in steps:
        if not isinstance(step, ProofStep) or not isinstance(
            step.sibling, (bytes, bytearray, memoryview)
        ):
            return False
        sibling = bytes(step.sibling)
        expected_side = "right" if index % 2 == 0 else "left"
        if step.side != expected_side or len(sibling) != 32:
            return False
        if index % 2 == 0 and index + 1 >= width:
            if not hmac.compare_digest(sibling, value):
                return False
        value = (
            _node_hash(value, sibling)
            if step.side == "right"
            else _node_hash(sibling, value)
        )
        index //= 2
        width = (width + 1) // 2
    if width != 1 or index != 0:
        return False
    return hmac.compare_digest(_root_hash(leaf_count, value), root)
