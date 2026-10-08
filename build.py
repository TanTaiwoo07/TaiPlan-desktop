"""一键打包 TaiPlan（PyInstaller --onedir）。

用法::

    .venv\\Scripts\\python.exe build.py            # Release：dist/TaiPlan/TaiPlan.exe（无控制台）
    .venv\\Scripts\\python.exe build.py --debug    # Debug  ：dist/TaiPlan-Debug/TaiPlan-Debug.exe（带控制台）

流程（对应第 15 阶段文档第 33 节）
---------------------------------
1) 检查 PyInstaller     2) 清理 build/
3) 清理旧 dist/<name>/  4) **绝不触碰 LOCALAPPDATA**
5) 调用 PyInstaller     6) 保存 build_logs/pyinstaller.log
7) 检查 exe 是否生成    8) 扫描 dist 里的敏感文件
9) 跑 smoke test（临时数据目录）  10) 输出最终路径
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import packaging_tools  # noqa: E402
import version as app_version  # noqa: E402

BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_LOGS = PROJECT_ROOT / "build_logs"
SPEC_FILE = PROJECT_ROOT / "TaiPlan.spec"

STEP_TOTAL = 10


def log(step, msg):
    if step:
        print(f"[{step}/{STEP_TOTAL}] {msg}")
    else:
        print(f"        {msg}")


def fail(msg, code=1):
    print(f"\n*** 构建失败: {msg}")
    return code


def _assert_inside_project(path: Path) -> None:
    """硬保护：任何要删除的路径都必须落在项目目录内。

    LOCALAPPDATA\\TaiPlan 永远不会在这个范围内，所以不可能被误删。
    """
    p = Path(path).resolve()
    root = PROJECT_ROOT.resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise SystemExit(f"拒绝删除项目目录之外的路径: {p}")
    # 再明确挡一次用户数据目录
    try:
        import app_paths

        data_dir = Path(app_paths.get_user_data_dir()).resolve()
        if data_dir == p or (data_dir in p.parents):
            raise SystemExit(f"拒绝删除用户数据目录: {p}")
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        pass


def step1_check_pyinstaller():
    log(1, "检查 PyInstaller ...")
    try:
        import PyInstaller  # noqa: F401

        ver = getattr(__import__("PyInstaller"), "__version__", "unknown")
        log(None, f"PyInstaller {ver}  (python: {sys.executable})")
        return ver
    except ImportError:
        print("\n未安装 PyInstaller。请先执行：")
        print(f'  "{sys.executable}" -m pip install --no-user pyinstaller')
        raise SystemExit(1)


def step2_clean_build():
    log(2, "清理 build/ ...")
    if BUILD_DIR.exists():
        _assert_inside_project(BUILD_DIR)
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
        log(None, f"已删除 {BUILD_DIR}")
    else:
        log(None, "build/ 不存在，跳过")


def step3_clean_dist(base_name):
    log(3, f"清理旧 dist/{base_name}/ ...")
    target = DIST_DIR / base_name
    if target.exists():
        _assert_inside_project(target)
        shutil.rmtree(target, ignore_errors=True)
        log(None, f"已删除 {target}")
    else:
        log(None, f"dist/{base_name}/ 不存在，跳过")


def step4_guard_userdata():
    log(4, "确认不会触碰用户数据（LOCALAPPDATA）...")
    try:
        import app_paths

        data_dir = Path(app_paths.get_user_data_dir())
        database_path = Path(app_paths.get_database_path())
        log(None, f"用户数据目录: {data_dir}")
        log(None, f"用户数据库  : {database_path}")
        inside = False
        try:
            data_dir.resolve().relative_to(PROJECT_ROOT.resolve())
            inside = True
        except ValueError:
            pass
        if inside:
            raise SystemExit("用户数据目录竟然在项目目录内，请先检查 app_paths 配置")
        log(None, "用户数据目录在项目目录之外，构建过程不会读写它 ✔")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        log(None, f"（跳过用户数据路径检查: {type(exc).__name__}: {exc}）")


def step5_run_pyinstaller(base_name, debug):
    log(5, f"调用 PyInstaller（{'debug' if debug else 'release'}，onedir）...")
    BUILD_LOGS.mkdir(exist_ok=True)
    log_path = BUILD_LOGS / "pyinstaller.log"
    env = dict(os.environ)
    env["TODO_APP_DEBUG"] = "1" if debug else "0"
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [sys.executable, "-m", "PyInstaller", str(SPEC_FILE),
           "--noconfirm", "--distpath", str(DIST_DIR), "--workpath", str(BUILD_DIR),
           "--log-level", "INFO"]
    log(None, "命令: " + " ".join(cmd))
    t0 = time.time()
    with open(log_path, "wb") as fh:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        fh.write(proc.stdout or b"")
    # 把关键告警回显出来，方便排查 hidden import / DLL 缺失
    text = (proc.stdout or b"").decode("utf-8", "replace")
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("error", "missing module", "missing dll",
                                  "not found", "warning: hidden import")):
            print("        | " + line[:180])
    log(None, f"耗时 {time.time() - t0:.1f}s，日志: {log_path}")
    return log_path, proc.returncode


def step7_check_exe(base_name, exe_name):
    log(7, "检查可执行文件 ...")
    exe_path = DIST_DIR / base_name / exe_name
    if not exe_path.is_file():
        raise SystemExit(f"未生成 {exe_path}（请查看 build_logs/pyinstaller.log）")
    size_mb = exe_path.stat().st_size / (1024 * 1024)
    log(None, f"{exe_path}  ({size_mb:.2f} MB)")
    internal = DIST_DIR / base_name / "_internal"
    log(None, f"_internal/ 存在: {internal.is_dir()}")
    return exe_path


def _read_credential_probe():
    """拿真实凭据用于比对（只返回字符串，不打印）。取不到就返回 None。"""
    try:
        import ai_settings

        cfg, _ = ai_settings.load_config()
        cid = ai_settings.make_credential_id(
            cfg.get("api_type", ""), cfg.get("base_url", ""),
            cfg.get("model_id") or cfg.get("model", ""))
        return ai_settings.get_api_key(cid)
    except Exception:  # noqa: BLE001
        return None


def step8_scan_dist(base_name):
    log(8, "扫描 dist 里的敏感文件 / 密钥 ...")
    probe_value = _read_credential_probe()
    # 传入我们有意打包的第三方包前缀：这些目录里的示例字符串不算泄露，
    # 但真凭据原文与 todo.db 仍然会被抓到。
    findings = packaging_tools.scan_sensitive_files(
        DIST_DIR / base_name, probe=probe_value,
        vendor_prefixes=packaging_tools.BUNDLED_PACKAGE_PREFIXES)
    if not findings:
        log(None, f"未发现敏感文件（已用真实凭据比对: "
                  f"{'是' if probe_value else '否'}）✔")
        return []
    for f in findings:
        marker = "ERROR" if f["severity"] == "error" else "WARN "
        print(f"        {marker} {f['kind']}: {f['path']}")
    return findings


def step9_smoke_test(base_name, exe_name):
    log(9, "运行 --smoke-test（临时数据目录，不碰真实数据）...")
    exe_path = DIST_DIR / base_name / exe_name
    tmp = Path(tempfile.mkdtemp(prefix="todoapp_build_smoke_"))
    env = dict(os.environ)
    env["TODO_APP_DATA_DIR"] = str(tmp)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run([str(exe_path), "--smoke-test"], cwd=str(tmp),
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=300)
    out = (proc.stdout or b"").decode("utf-8", "replace")
    print("\n" + "=" * 62)
    print(out.rstrip())
    print("=" * 62 + "\n")
    shutil.rmtree(tmp, ignore_errors=True)
    return proc.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="打包 TaiPlan（PyInstaller onedir）")
    parser.add_argument("--debug", action="store_true", help="生成带控制台的 Debug 版")
    parser.add_argument("--skip-smoke", action="store_true", help="跳过 smoke test")
    parser.add_argument("--skip-scan", action="store_true", help="跳过敏感文件扫描")
    args = parser.parse_args(argv)

    debug = bool(args.debug)
    exe_name = packaging_tools.DEBUG_EXE if debug else packaging_tools.RELEASE_EXE
    base_name = exe_name[:-4] if exe_name.lower().endswith(".exe") else exe_name

    print("=" * 62)
    print(f"TaiPlan 打包  version={app_version.__version__}  "
          f"channel={getattr(app_version, 'BUILD_CHANNEL', '?')}")
    print(f"模式: {'DEBUG（带控制台）' if debug else 'RELEASE（无控制台）'}  onedir")
    print(f"项目: {PROJECT_ROOT}")
    print("=" * 62)

    ver = step1_check_pyinstaller()
    step2_clean_build()
    step3_clean_dist(base_name)
    step4_guard_userdata()
    log_path, rc = step5_run_pyinstaller(base_name, debug)
    if rc != 0:
        return fail(f"PyInstaller 退出码 {rc}，日志: {log_path}", rc)
    log(6, f"PyInstaller 日志已保存: {log_path}")

    exe_path = step7_check_exe(base_name, exe_name)

    findings = [] if args.skip_scan else step8_scan_dist(base_name)
    if findings:
        print()
        print("*** dist 中发现敏感文件，构建判定为失败 ***")
        return fail("dist 含有用户数据 / 密钥")

    if args.skip_smoke:
        log(9, "已按参数跳过 smoke test")
        smoke_rc = 0
    else:
        smoke_rc = step9_smoke_test(base_name, exe_name)
        if smoke_rc != 0:
            return fail(f"smoke test 退出码 {smoke_rc}")

    log(10, "完成")
    print(f"        最终可执行文件: {exe_path}")
    print(f"        目录: {exe_path.parent}")
    print(f"        PyInstaller 版本: {ver}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
