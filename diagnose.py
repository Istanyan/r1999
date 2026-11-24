"""
game_bot 诊断工具 — 截图环境/模板库/OCR/页面判定 一次体检。

用法:
    python diagnose.py                    # 全量诊断(截图+全模板+OCR+页面判定)
    python diagnose.py --tpl fanhui       # 只查单张模板
    python diagnose.py --no-ocr           # 跳过 OCR(未装 paddle 时)

输出:
    - 控制台报告(✓/⚠/✗ 分级)
    - temp/diag_report/ 下保存:全屏截图、每张模板的最佳匹配区域裁剪
      (低置信度的会标注,肉眼扫一眼即可发现"匹配到的是不是真按钮")

建议每天首跑前先跑一次本工具(约 30 秒),模板过期/皮肤变化当场暴露。
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import cv2

from core.matcher import _imread
from core.screenshot import Screenshot, find_window_rect

OUT_DIR = "temp/diag_report"
CONF_OK = 0.85      # ✓ 正常
CONF_WARN = 0.70    # ⚠ 低置信(可能过期/被皮肤影响)
CONF_FAIL = 0.55    # ✗ 基本失效


def section(title):
    print(f"\n{'=' * 12} {title} {'=' * 12}")


def diag_environment(args):
    section("1. 截图环境")
    found = find_window_rect(args.window)
    if found is None:
        print(f"✗ 未找到窗口 '{args.window}'——请确认模拟器已启动")
        return None
    rect, hwnd = found
    print(f"✓ 窗口 '{args.window}' HWND={hwnd} 区域={rect}")
    ss = Screenshot(window_title=args.window)
    img = ss.capture()
    os.makedirs(OUT_DIR, exist_ok=True)
    full_path = os.path.join(OUT_DIR, "screen.png")
    cv2.imwrite(full_path, img)
    # 全黑/全白检查(截到别的窗口或锁屏时典型表现)
    mean = float(img.mean())
    print(f"✓ 截图 {img.shape[1]}x{img.shape[0]} 均值={mean:.1f}", end="")
    if mean < 5 or mean > 250:
        print("  ✗ 画面异常(全黑/全白)——可能截到了别的窗口!")
        return None
    print("  ✓ 正常")
    print(f"  截图已保存 {full_path}")
    return img


def diag_templates(img, args):
    section("2. 模板库扫描")
    tpl_dir = os.path.join("templates", args.game)
    if not os.path.isdir(tpl_dir):
        print(f"✗ 模板目录不存在: {tpl_dir}")
        return
    names = sorted(os.listdir(tpl_dir))
    if args.tpl:
        names = [n for n in names if args.tpl in n]
    match_dir = os.path.join(OUT_DIR, "match")
    os.makedirs(match_dir, exist_ok=True)

    stale, ok_count = [], 0
    for name in names:
        if not name.lower().endswith(".png"):
            continue
        tpl = _imread(os.path.join(tpl_dir, name))
        if tpl is None:
            print(f"✗ {name}: 模板文件损坏")
            continue
        th, tw = tpl.shape[:2]
        if th > img.shape[0] or tw > img.shape[1]:
            print(f"✗ {name}: 模板({tw}x{th})大于截图,跳过")
            continue
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        status = "✓" if mv >= CONF_OK else ("⚠" if mv >= CONF_WARN else "✗")
        if mv >= CONF_OK:
            ok_count += 1
        else:
            stale.append((name, mv))
        print(f"{status} {name}: conf={mv:.3f} at {ml}")
        # 保存最佳匹配区域裁剪(低置信度重点目检)
        x, y = ml
        pad = 10
        crop = img[max(0, y - pad):y + th + pad, max(0, x - pad):x + tw + pad]
        tag = "LOW" if mv < CONF_OK else "OK"
        cv2.imwrite(os.path.join(match_dir, f"{tag}_{name}"), crop)

    print(f"\n小结: {ok_count}/{len(names)} 张达到 {CONF_OK};"
          f" {len(stale)} 张低于 {CONF_WARN}")
    if stale:
        print("⚠ 以下模板疑似过期/受皮肤影响,建议重制或加 OCR 兜底:")
        for name, mv in stale:
            print(f"   - {name} (conf={mv:.3f})")
    print(f"匹配区域裁剪已保存 {match_dir}/ (LOW_ 前缀为重点目检对象)")


def diag_ocr(img, args):
    section("3. OCR 全文")
    if args.no_ocr:
        print("(跳过 --no-ocr)")
        return
    try:
        from core.ocr import OCR
        ocr = OCR(lang="ch")
    except Exception as e:
        print(f"✗ OCR 初始化失败: {e}")
        return
    items = ocr.recognize(img)
    if not items:
        print("(未识别到任何文字)")
        return
    for it in items:
        print(f"  '{it['text']}' conf={it['confidence']:.2f} @ {it['center']}")
    print(f"共 {len(items)} 条")


def diag_page_checks(img, args):
    section("4. 页面判定函数实测")
    try:
        from core.game_bot import GameBot
        bot = GameBot(window_title=args.window, game_name=args.game)
    except Exception as e:
        print(f"✗ GameBot 初始化失败: {e}")
        return
    # 实时截图的判定(不是诊断开头的截图,保证判定函数走自己的截图链路)
    checks = [
        ("主界面 _on_main_screen", "startup", "_on_main_screen"),
        ("征战天下页 _zz_is_on_page", "zhengzhan", "_zz_is_on_page"),
    ]
    for label, mixin_mod, method in checks:
        try:
            script = bot
            # 判定函数都在 Mixin 上,通过 SanguoshaScript 实例调用
            from games.sanguosha import SanguoshaScript
            script = SanguoshaScript()
            fn = getattr(script, method, None)
            if fn is None:
                print(f"- {label}: 方法不存在,跳过")
                continue
            ok = fn(bot)
            print(f"{'✓' if ok else '✗'} {label}: {ok}")
        except Exception as e:
            print(f"✗ {label}: 异常 {e!r}")


def main():
    parser = argparse.ArgumentParser(description="game_bot 诊断工具")
    parser.add_argument("--window", default="MuMu", help="窗口标题关键字")
    parser.add_argument("--game", default="sanguosha", help="游戏名(模板子目录)")
    parser.add_argument("--tpl", default=None, help="只诊断文件名包含此关键字的模板")
    parser.add_argument("--no-ocr", action="store_true", help="跳过 OCR 环节")
    args = parser.parse_args()

    t0 = time.time()
    print("game_bot 诊断报告")
    img = diag_environment(args)
    if img is None:
        print("\n截图环境异常,后续环节跳过。修复后重跑。")
        return
    diag_templates(img, args)
    diag_ocr(img, args)
    diag_page_checks(img, args)
    print(f"\n总耗时 {time.time() - t0:.1f}s。报告目录: {OUT_DIR}/")


if __name__ == "__main__":
    main()
