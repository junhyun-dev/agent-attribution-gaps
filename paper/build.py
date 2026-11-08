"""Assemble report.html from the style in report-draft1.html and a body file, then print it to PDF.
Usage: python3 build.py body3.html report.pdf [--author "Name · Affiliation"] [--code "line"]"""
import argparse, glob, os, re, subprocess
ap = argparse.ArgumentParser()
ap.add_argument("body"); ap.add_argument("pdf")
ap.add_argument("--author"); ap.add_argument("--code")
a = ap.parse_args()
style = re.search(r"<style>.*?</style>", open("report-draft1.html", encoding="utf-8").read(), re.S).group(0)
body = open(a.body, encoding="utf-8").read()
if a.author:
    body = body.replace('<div class="authors">[Author name] · Independent</div>', f'<div class="authors">{a.author}</div>')
if a.code:
    body = re.sub(r"<li>Code: \[GitHub link[^<]*</li>", f"<li>{a.code}</li>", body)
html = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Who Wrote This? Attribution Gaps in a Shared Workspace of Coding Agents</title>'
        + style + "</head><body>" + body + "</body></html>")
out_html = os.path.splitext(a.pdf)[0] + ".html"
open(out_html, "w", encoding="utf-8").write(html)
chrome = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))[-1]
subprocess.run([chrome, "--no-sandbox", f"--print-to-pdf={os.path.abspath(a.pdf)}", "--no-pdf-header-footer", "file://" + os.path.abspath(out_html)],
               check=True, capture_output=True, timeout=120)
print("ok", a.pdf)
