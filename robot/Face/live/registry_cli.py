"""Administer the local institutional Face registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .identity_registry import IdentityRegistry, REGISTRY_PATH, REGISTRY_RANKS

DUPLICATE_COSINE = 0.45  # same threshold the live recognizer uses to call a face KNOWN


def face_embedding(photo: Path):
    """Largest face in the photo -> (L2-normalized 512-D embedding, face size in px)."""
    import numpy as np
    from src.uniface_runner import build_analyzer, read_bgr

    image = read_bgr(photo)
    if image is None:
        raise SystemExit(f"Could not read image: {photo}")
    faces = build_analyzer().analyze(image)
    faces = [f for f in faces if getattr(f, "embedding", None) is not None]
    if not faces:
        raise SystemExit(f"No face found in {photo}")
    face = max(faces, key=lambda f: abs(f.bbox[2] - f.bbox[0]) * abs(f.bbox[3] - f.bbox[1]))
    vector = np.asarray(face.embedding, dtype=np.float32).reshape(-1)
    return vector / float(np.linalg.norm(vector)), int(abs(face.bbox[2] - face.bbox[0])), len(faces)


def enroll(registry: IdentityRegistry, args) -> dict:
    from src.uniface_runner import sha256_file

    vector, face_px, n_faces = face_embedding(args.photo)
    gallery = registry.load_gallery(registry.active_encoder_key())
    closest = None
    if gallery:
        scores = {pid: float(entry["embedding"] @ vector) for pid, entry in gallery.items()}
        pid = max(scores, key=scores.get)
        closest = {"person_id": pid, "display_name": gallery[pid]["display_name"], "cosine": round(scores[pid], 3)}
        if scores[pid] >= DUPLICATE_COSINE and not args.force:
            raise SystemExit(f"Face already matches {closest}; refusing to enroll (use --force to override)")
    person_id = args.person_id or registry.next_person_id()
    result = registry.enroll_person(person_id, args.name, args.rank_code, vector,
                                    sha256_file(args.photo), changed_by=args.changed_by)
    return {**result, "face_px": face_px, "faces_in_photo": n_faces, "closest_existing": closest}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=REGISTRY_PATH, help="Operational SQLite path")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List registered people and current ranks")
    get = commands.add_parser("get", help="Show one registered profile")
    get.add_argument("person_id")
    set_rank = commands.add_parser("set-rank", help="Change a person's rank and record the change")
    set_rank.add_argument("person_id")
    set_rank.add_argument("rank_code", choices=tuple(REGISTRY_RANKS))
    set_rank.add_argument("--changed-by", default="local-admin")
    add = commands.add_parser("enroll", help="Add a new person from a photo (face template + rank)")
    add.add_argument("name", help="Display name used by the robot")
    add.add_argument("rank_code", choices=tuple(REGISTRY_RANKS))
    add.add_argument("--photo", type=Path, required=True, help="Frontal photo; the largest face is used")
    add.add_argument("--person-id", help="Defaults to the next free P###")
    add.add_argument("--changed-by", default="local-admin")
    add.add_argument("--force", action="store_true", help="Enroll even if the face matches someone")
    args = parser.parse_args()

    if not args.db.is_file():
        raise SystemExit(f"Registry not found: {args.db}")
    registry = IdentityRegistry(args.db)
    if args.command == "list":
        result = registry.list_people()
    elif args.command == "get":
        result = registry.get_person(args.person_id)
        if result is None:
            raise SystemExit(f"Unknown person_id: {args.person_id}")
    elif args.command == "enroll":
        result = enroll(registry, args)
    else:
        result = registry.set_person_rank(args.person_id, args.rank_code, changed_by=args.changed_by)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
