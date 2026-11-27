import io
import json
import unittest
import warnings
import zipfile

from circuitmind.circuit_schema import model_digest, new_circuit_model
from circuitmind.project_archive import read_project_archive


def archive_with(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return stream.getvalue()


class ProjectArchiveTests(unittest.TestCase):
    def valid_entries(self):
        project_id = "a" * 12
        project = {"project_id": project_id, "name": "Backup", "architecture": "discrete",
                   "created_at": "2026-01-01T00:00:00+00:00", "findings": [], "inventory": [], "validation": {}}
        return project_id, [
            (f"{project_id}/project.json", json.dumps(project)),
            (f"{project_id}/circuit_model.json", json.dumps(new_circuit_model())),
            (f"{project_id}/artifact.json", "{}"),
        ]

    def test_valid_archive_returns_relative_files_and_validated_model(self):
        project_id, entries = self.valid_entries()
        source, project, files = read_project_archive(archive_with(entries))
        self.assertEqual(source, project_id)
        self.assertEqual(project["name"], "Backup")
        self.assertIn("circuit_model.json", files)
        self.assertIn("artifact.json", files)
        self.assertNotIn(f"{project_id}/circuit_model.json", files)

    def test_rejects_traversal_multiple_roots_and_invalid_topology(self):
        project_id, entries = self.valid_entries()
        traversal = entries + [(f"{project_id}/../escape.txt", "bad")]
        with self.assertRaisesRegex(ValueError, "path"):
            read_project_archive(archive_with(traversal))
        with self.assertRaisesRegex(ValueError, "exactly one project root"):
            read_project_archive(archive_with(entries + [("b" * 12 + "/extra.txt", "bad")]))
        invalid_model = new_circuit_model(); invalid_model["schema_version"] = 9
        invalid = [(name, json.dumps(invalid_model) if name.endswith("circuit_model.json") else content)
                   for name, content in entries]
        with self.assertRaisesRegex(ValueError, "failed schema/topology"):
            read_project_archive(archive_with(invalid))

    def test_snapshot_manifest_requires_present_topology_valid_hash_linked_models(self):
        project_id, entries = self.valid_entries()
        model = new_circuit_model()
        digest = model_digest(model)
        snapshot_id = f"20260101T000000Z-{digest[:12]}"
        manifest = {"project_id": project_id, "snapshots": [
            {"snapshot_id": snapshot_id, "created_at": "2026-01-01T00:00:00+00:00", "sha256": digest}
        ]}
        valid = entries + [(f"{project_id}/snapshots/manifest.json", json.dumps(manifest)),
                           (f"{project_id}/snapshots/{snapshot_id}.json", json.dumps(model))]
        read_project_archive(archive_with(valid))
        manifest["snapshots"][0]["sha256"] = "0" * 64
        corrupted = entries + [(f"{project_id}/snapshots/manifest.json", json.dumps(manifest)),
                               (f"{project_id}/snapshots/{snapshot_id}.json", json.dumps(model))]
        with self.assertRaisesRegex(ValueError, "hash validation"):
            read_project_archive(archive_with(corrupted))

    def test_rejects_duplicate_and_case_colliding_paths(self):
        project_id, entries = self.valid_entries()
        duplicate = entries + [(f"{project_id}/artifact.json", "second")]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            duplicate_archive = archive_with(duplicate)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            read_project_archive(duplicate_archive)
        colliding = entries + [(f"{project_id}/ARTIFACT.JSON", "second")]
        with self.assertRaisesRegex(ValueError, "duplicate or case-colliding"):
            read_project_archive(archive_with(colliding))


if __name__ == "__main__":
    unittest.main()
