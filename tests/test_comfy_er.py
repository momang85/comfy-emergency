import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

import comfy_er


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8-sig"))


class WorkflowTests(unittest.TestCase):
    def test_ui_workflow_custom_node_and_models(self):
        doc = load_fixture("workflow_ui.json")
        parsed = comfy_er.parse_workflow_doc(doc)
        self.assertEqual(parsed["format"], "UI")
        self.assertEqual(parsed["subgraphs"], 1)
        self.assertIn("ImpactWildcardProcessor", parsed["required"])
        self.assertIn("CheckpointLoaderSimple", parsed["required"])
        self.assertNotIn("workflow>国风细节增强", parsed["required"])
        self.assertIn("ImageScaleBy", parsed["required"])
        names = {item["name"] for item in parsed["models"].values()}
        self.assertIn("国风大模型_v3.safetensors", names)
        self.assertIn("水墨风格_细节增强.safetensors", names)
        self.assertIn("国风_vae.safetensors", names)
        self.assertTrue(all(item["directory"] != "未知" for item in parsed["models"].values()))

    def test_ui_analysis_does_not_report_core_nodes(self):
        data = comfy_er.analyze_workflow(FIXTURES / "workflow_ui.json", offline=True)
        missing = {node["class"] for node in data["missing_nodes"]}
        self.assertIn("ImpactWildcardProcessor", missing)
        parsed = comfy_er.parse_workflow_doc(load_fixture("workflow_ui.json"))
        for node_type in parsed["required"]:
            if node_type in comfy_er.CORE_NODES:
                self.assertNotIn(node_type, missing)
        self.assertEqual(data["subgraphs"], 1)
        model_names = {item["name"] for item in data["missing_models"]}
        self.assertIn("国风大模型_v3.safetensors", model_names)
        self.assertIn("水墨风格_细节增强.safetensors", model_names)

    def test_api_workflow(self):
        data = comfy_er.analyze_workflow(FIXTURES / "workflow_api.json", offline=True)
        self.assertEqual(data["format"], "API")
        missing = {node["class"] for node in data["missing_nodes"]}
        self.assertIn("ImpactWildcardProcessor", missing)
        self.assertNotIn("KSampler", missing)
        loras = [model for model in data["missing_models"] if model["directory"] == "loras"]
        self.assertEqual(len(loras), 1)
        self.assertEqual(loras[0]["name"], "水墨风格_细节增强.safetensors")

    def test_api_sampler_name_is_not_a_model(self):
        doc = {"1": {"class_type": "KSampler", "inputs": {
            "sampler_name": "euler", "scheduler": "normal",
            "ckpt_name": "real-checkpoint.safetensors"}}}
        parsed = comfy_er.parse_workflow_doc(doc)
        names = {item["name"] for item in parsed["models"].values()}
        self.assertEqual(names, {"real-checkpoint.safetensors"})

    def test_directory_scan_skips_non_workflow_json(self):
        data = comfy_er.analyze_workflow(FIXTURES, offline=True)
        self.assertTrue(data["is_dir"])
        self.assertGreaterEqual(len(data["files"]), 2)
        self.assertTrue(any("pip_list_conflict.json" in warning for warning in data["warnings"]))

    def test_offline_missing_node_wording(self):
        parsed = comfy_er.parse_workflow_doc(load_fixture("workflow_ui.json"))
        node_map = {"available": False, "class_index": {}}
        missing = comfy_er.build_missing_nodes(parsed["required"], node_map, {}, set())
        impact = [node for node in missing if node["class"] == "ImpactWildcardProcessor"][0]
        self.assertIsNone(impact["repo_url"])
        self.assertIn("未联网，节点归属未知", impact["reason"])


class RuleTests(unittest.TestCase):
    def test_conflict_fixture_hits_numpy_and_xformers(self):
        items = load_fixture("pip_list_conflict.json")
        packages = {item["name"].casefold(): item["version"] for item in items}
        findings = comfy_er.evaluate_rules(packages, [], python_exe="python", gpu_present=False)
        ids = {item["id"] for item in findings}
        self.assertIn("numpy2-torch", ids)
        self.assertIn("xformers-torch", ids)
        self.assertIn("transformers-new", ids)
        numpy = [item for item in findings if item["id"] == "numpy2-torch"][0]
        self.assertEqual(numpy["severity"], "error")
        self.assertIn("numpy<2", numpy["fix"])

    def test_no_false_positive_for_consistent_torch(self):
        packages = {"torch": "2.4.0", "numpy": "1.26.4", "torchvision": "0.19.0",
                    "xformers": "0.0.29"}
        findings = comfy_er.evaluate_rules(packages, [], python_exe="python", gpu_present=False)
        ids = {item["id"] for item in findings}
        self.assertNotIn("numpy2-torch", ids)
        self.assertNotIn("xformers-torch", ids)
        self.assertNotIn("torchvision-torch", ids)

    def test_missing_requirements_and_old_pydantic_node(self):
        node = {
            "name": "ComfyUI-Example",
            "path": str(FIXTURES / "fake_custom_nodes" / "ComfyUI-Example"),
            "urls": [], "disabled": False, "requirements": True,
        }
        packages = {"pydantic": "2.9.2"}
        findings = comfy_er.evaluate_rules(packages, [node], python_exe="python",
                                           gpu_present=False)
        ids = {item["id"] for item in findings}
        self.assertIn("missing-requirements", ids)
        self.assertIn("pydantic2-old-nodes", ids)
        missing = [item for item in findings if item["id"] == "missing-requirements"][0]
        self.assertIn("onnxruntime", missing["why"])


class LogTests(unittest.TestCase):
    def test_broken_startup_has_at_least_three_hits(self):
        text = (FIXTURES / "startup_broken.log").read_text(encoding="utf-8-sig")
        hits = comfy_er.scan_log(text, "python")
        self.assertGreaterEqual(len(hits), 3)
        ids = {hit["id"] for hit in hits}
        self.assertIn("import-failed", ids)
        self.assertIn("module-missing", ids)
        self.assertIn("numpy-abi", ids)
        for hit in hits:
            self.assertTrue(hit["line"])
            self.assertTrue(hit["cause"])
            self.assertTrue(hit["fix"])
            self.assertIn(hit["confidence"], ("高", "中", "低"))

    def test_owner_is_extracted_from_path(self):
        line = r"Cannot import D:\秋叶整合包\ComfyUI\custom_nodes\ComfyUI-Impact-Pack module for custom nodes: No module named 'impact'"
        self.assertEqual(comfy_er._owner_from_line(line), "ComfyUI-Impact-Pack")

    def test_unknown_log_is_honest(self):
        self.assertEqual(comfy_er.scan_log("一切正常，没有堆栈。"), [])


class DataTests(unittest.TestCase):
    def test_node_map_index(self):
        data = {
            "https://github.com/owner/Demo-Nodes": [
                ["DemoNodeA", "DemoNodeB"], {"title_aux": "Demo 节点"}
            ]
        }
        parsed = comfy_er.parse_node_map(data)
        self.assertEqual(parsed["repo_count"], 1)
        self.assertEqual(parsed["class_count"], 2)
        self.assertEqual(parsed["class_index"]["DemoNodeA"]["title"], "Demo 节点")

    def test_extra_model_paths_subset(self):
        parsed = comfy_er.read_extra_model_paths(FIXTURES / "extra_model_paths.yaml")
        self.assertTrue(parsed["present"])
        self.assertEqual(len(parsed["sections"]), 2)
        first = parsed["sections"][0]
        self.assertEqual(first["name"], "aki")
        self.assertEqual(first["base_path"], r"D:\秋叶整合包\ComfyUI")
        self.assertIn("loras", first["paths"])
        self.assertIn("D:\\模型仓库\\LoRA", first["paths"]["loras"])

    def test_mixed_encoding_log(self):
        gbk = "错误：找不到模型 国风.safetensors".encode("gbk")
        utf8 = "报错：模块缺失".encode("utf-8")
        raw = gbk + b"\n" + utf8 + b"\n"
        decoded = comfy_er.decode_bytes(raw)
        self.assertIn("国风.safetensors", decoded)
        self.assertIn("模块缺失", decoded)

    def test_model_presence_is_case_insensitive_and_subdir_aware(self):
        models = {
            ("checkpoints", "x.safetensors"): {
                "name": "子目录/国风.SAFETENSORS", "basename": "国风.SAFETENSORS",
                "directory": "checkpoints", "sources": ["x"], "node_types": ["y"],
            }
        }
        inventory = {"available": True, "count": 1,
                     "names": {"国风.safetensors"},
                     "rels": {"子目录/国风.safetensors"}}
        self.assertEqual(comfy_er.model_presence(models, inventory), [])


class DoctorTests(unittest.TestCase):
    def test_missing_comfy_is_clean(self):
        missing = str(ROOT / "definitely-not-a-comfyui-install")
        data = comfy_er.build_doctor_report(missing)
        self.assertIsNone(data["comfy"]["path"])
        self.assertIn("没找到 ComfyUI", data["conclusion"])
        self.assertTrue(data["next_steps"])

    def test_report_markdown_contains_share_instructions(self):
        data = comfy_er.build_doctor_report(str(ROOT / "definitely-not-a-comfyui-install"))
        markdown = comfy_er.render_report(data)
        self.assertIn("# ComfyUI 诊断报告", markdown)
        self.assertIn("复制到群里", markdown)
        self.assertIn(comfy_er.REPO_URL, markdown)
        self.assertIn("## 冲突规则", markdown)


class CliEncodingTests(unittest.TestCase):
    def run_cli(self, args, io_encoding):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = io_encoding
        return subprocess.run(
            [sys.executable, str(ROOT / "comfy_er.py")] + args,
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_json_is_utf8_even_when_console_is_gbk(self):
        proc = self.run_cli(
            ["workflow", str(FIXTURES / "workflow_ui.json"), "--offline", "--json"], "gbk")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.decode("utf-8"))
        names = [item["name"] for item in data["missing_models"]]
        self.assertIn("国风大模型_v3.safetensors", names)

    def test_gbk_console_preserves_chinese(self):
        proc = self.run_cli(
            ["workflow", str(FIXTURES / "workflow_ui.json"), "--offline"], "gbk")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = proc.stdout.decode("gbk")
        self.assertIn("国风大模型_v3.safetensors", text)
        self.assertIn("未联网，节点归属未知", text)


class SelfTestTests(unittest.TestCase):
    def test_self_test_exits_zero_without_network(self):
        self.assertEqual(comfy_er.run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
