"""
数据验证工具模块
"""

import re
import ipaddress
import hashlib
from typing import Optional
from urllib.parse import urlparse


def is_valid_ip(ip_str: str) -> bool:
    """
    验证IP地址是否有效

    Args:
        ip_str: IP地址字符串

    Returns:
        bool: 是否为有效IP地址
    """
    try:
        ipaddress.ip_address(ip_str)
        return True
    except ValueError:
        return False


def is_valid_url(url: str) -> bool:
    """
    验证URL是否有效

    Args:
        url: URL字符串

    Returns:
        bool: 是否为有效URL
    """
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False


def extract_domain(url: str) -> Optional[str]:
    """
    从URL中提取域名

    Args:
        url: URL字符串

    Returns:
        Optional[str]: 域名，如果提取失败返回None
    """
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return None


def is_chinese_text(text: str) -> bool:
    """
    检测文本是否主要包含中文字符

    Args:
        text: 待检测的文本

    Returns:
        bool: 如果文本主要是中文返回True，否则返回False
    """
    if not text:
        return False

    # 中文字符的Unicode范围
    chinese_pattern = re.compile(r'[\u4e00-\u9fff]+')
    chinese_chars = len(chinese_pattern.findall(text))
    total_chars = len(text.strip())

    if total_chars == 0:
        return False

    # 如果中文字符占比超过30%，认为是中文文本
    return (chinese_chars / total_chars) > 0.3


def sanitize_filename(filename: str, max_length: int = 200, max_total_length: int = 250) -> str:
    """
    清理文件名，移除非法字符并确保跨平台兼容性

    Args:
        filename: 原始文件名
        max_length: 单个部分的最大长度限制（如描述部分）
        max_total_length: 完整文件名的最大长度限制（确保跨平台兼容性）

    Returns:
        str: 清理后的安全文件名
    """
    if not filename:
        return "untitled"

    # 移除或替换非法字符 (Windows/macOS/Linux 通用)
    # Windows: < > : " / \ | ? *
    # macOS: : (在文件系统中转换为/)
    # Linux: / 和 null字符
    illegal_chars = r'[<>:"/\\|?*\x00-\x1f\x7f]'
    filename = re.sub(illegal_chars, "_", filename)

    # 移除控制字符和特殊字符
    filename = re.sub(r'[\x00-\x1f\x7f-\x9f]', "_", filename)

    # 移除多余的空格和点
    filename = re.sub(r'\s+', ' ', filename).strip()
    filename = filename.strip('.')

    # 避免Windows保留名称
    reserved_names = {
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    }
    if filename.upper() in reserved_names:
        filename = f"_{filename}"

    # 首先按照单个部分长度限制截断
    if len(filename) > max_length:
        filename = filename[:max_length-3] + "..."

    # 确保总长度符合要求（考虑可能的扩展名和路径）
    # 一般来说：
    # Windows: 260字符路径限制，但文件名部分建议不超过255
    # macOS: 255字符文件名限制
    # Linux: 255字节文件名限制（UTF-8编码可能影响实际字符数）

    # 计算字节长度（考虑UTF-8编码）
    byte_length = len(filename.encode('utf-8'))

    # 如果字节长度超过限制，需要智能截断
    if byte_length > max_total_length:
        # 按字符逐步截断，直到字节长度符合要求
        while byte_length > max_total_length - 3 and len(filename) > 10:
            filename = filename[:-1]
            byte_length = len(filename.encode('utf-8'))
        filename = filename + "..."

    # 确保不以点或空格结尾（Windows限制）
    filename = filename.rstrip(' .')

    return filename or "untitled"


def validate_filename_length(filename: str) -> dict:
    """
    验证文件名长度是否符合跨平台要求

    Args:
        filename: 待验证的文件名

    Returns:
        dict: 验证结果，包含是否有效和详细信息
    """
    if not filename:
        return {"valid": False, "reason": "文件名为空"}

    char_length = len(filename)
    byte_length = len(filename.encode('utf-8'))

    # 跨平台文件名长度限制
    limits = {
        "windows_path": 260,    # Windows路径总长度限制
        "windows_filename": 255,  # Windows文件名长度限制
        "macos_filename": 255,   # macOS文件名长度限制
        "linux_filename": 255,   # Linux文件名字节长度限制
        "safe_length": 200       # 保守的安全长度
    }

    issues = []

    if char_length > limits["safe_length"]:
        issues.append(f"字符长度({char_length})超过安全限制({limits['safe_length']})")

    if byte_length > limits["linux_filename"]:
        issues.append(f"字节长度({byte_length})超过Linux限制({limits['linux_filename']})")

    if char_length > limits["windows_filename"]:
        issues.append(f"字符长度({char_length})超过Windows限制({limits['windows_filename']})")

    return {
        "valid": len(issues) == 0,
        "char_length": char_length,
        "byte_length": byte_length,
        "issues": issues,
        "limits": limits
    }


def generate_url_hash(url: str, platform: str = "") -> str:
    """
    为URL生成简短的哈希标识符

    Args:
        url: 原始URL
        platform: 平台名称（可选）

    Returns:
        str: 8位哈希字符串
    """
    # 将URL和平台信息组合
    content = f"{platform}:{url}" if platform else url

    # 生成MD5哈希
    hash_object = hashlib.md5(content.encode('utf-8'))
    hash_hex = hash_object.hexdigest()

    # 返回前8位作为短标识符
    return hash_hex[:8]