# -*- coding: utf-8 -*-
"""直接解析构建后 TaiPlan.exe 的 PE Version Info（不只看源码常量）。

为什么需要它：源码常量正确不代表产物正确（版本资源由打包器写入）。
本测试自己解析 PE 的 RT_VERSION 资源（无第三方依赖），逐字段校验。
"""
import io
import re
import struct
import unittest
from pathlib import Path

from taiplan import version  # noqa: E402

_VERSION = version.__version__

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "TaiPlan" / "TaiPlan.exe"

EXPECTED = {
    "CompanyName": "TaiWoo_Chen",
    "FileDescription": "TaiPlan",
    "FileVersion": _VERSION,
    "InternalName": "TaiPlan",
    "LegalCopyright": "\u00a9 2026 TaiWoo_Chen",
    "OriginalFilename": "TaiPlan.exe",
    "ProductName": "TaiPlan",
    "ProductVersion": _VERSION,
}
FORBIDDEN = ("TaiWooChen", "TaiwooChen", "Taiwoo_Chen", "Todo App", "TodoApp")


def _read_pe_version_strings(path: Path):
    """读取构建后 PE 的版本资源字符串（无第三方依赖）。

    步骤：解析 PE 头与节表 → 确认 .rsrc 里存在 RT_VERSION(16) 资源 →
    在该版本资源覆盖的字节范围内按 UTF-16LE 检索各字段键名并读取其后（4 字节对齐）的值。
    这样校验的是**产物二进制本身**，而不是源码常量。
    """
    data = path.read_bytes()

    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[e_lfanew:e_lfanew + 4] == b"PE\x00\x00", "不是有效的 PE 文件"
    coff = e_lfanew + 4
    n_sections = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20

    sec_off = opt + opt_size
    rsrc_ptr = rsrc_rva = None
    for i in range(n_sections):
        s0 = sec_off + i * 40
        if data[s0:s0 + 8].rstrip(b"\x00") == b".rsrc":
            rsrc_rva = struct.unpack_from("<I", data, s0 + 12)[0]
            rsrc_ptr = struct.unpack_from("<I", data, s0 + 20)[0]
            break
    assert rsrc_ptr is not None, "找不到 .rsrc 段"

    def entries(off):
        n_named, n_id = struct.unpack_from("<HH", data, off + 12)
        return [struct.unpack_from("<II", data, off + 16 + k * 8)
                for k in range(n_named + n_id)]

    has_version = any(eid == 16 for eid, _ in entries(rsrc_ptr))
    assert has_version, "PE 里没有 RT_VERSION 资源"

    # 版本资源位于 .rsrc 段内；在该段字节范围内检索字段
    blob = data[rsrc_ptr:rsrc_ptr + 0x4000]

    fields = ("CompanyName", "FileDescription", "FileVersion", "InternalName",
              "LegalCopyright", "OriginalFilename", "ProductName", "ProductVersion")
    out = {}
    for field in fields:
        needle = field.encode("utf-16-le") + b"\x00\x00"
        idx = blob.find(needle)
        if idx < 0:
            continue
        j = idx + len(needle)
        while j % 4:
            j += 1
        end = j
        while end + 1 < len(blob) and blob[end:end + 2] != b"\x00\x00":
            end += 2
        out[field] = blob[j:end].decode("utf-16-le", "replace")
    return out


class FrozenPeMetadataTest(unittest.TestCase):
    def setUp(self):
        if not EXE.is_file():
            self.fail(f"未找到构建产物 {EXE}；请先运行 build.py（本测试直接校验产物，"
                      "不做跳过）")
        self.strings = _read_pe_version_strings(EXE)

    def test_all_fields(self):
        for field, want in EXPECTED.items():
            self.assertEqual(self.strings.get(field), want,
                             f"PE 字段 {field} = {self.strings.get(field)!r}，应为 {want!r}")

    def test_no_misspelling_or_legacy_brand(self):
        joined = " ".join(f"{k}={v}" for k, v in self.strings.items())
        for bad in FORBIDDEN:
            self.assertNotIn(bad, joined, f"PE 元数据出现禁止字符串 {bad!r}")

    def test_author_underscore_kept(self):
        self.assertIn("_", self.strings.get("CompanyName", ""))
        self.assertIn("TaiWoo_Chen", self.strings.get("CompanyName", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
