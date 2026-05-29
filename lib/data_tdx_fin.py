"""TDX 财务报表数据（gpcw*.dat）解析器

数据格式（通达信 proprietary，已逆向破解）：
  - 文件：D:/TDX/vipdoc/cw/gpcwYYYYMMDD.dat
  - Header: 31,854 字节
    - offset 20 起：深圳股列表，每条 11 字节（6字节ASCII代码 + 5字节指针）
  - Data section: 从 header 之后开始
    - 前 2625 × 11 = 28,875 字节：上海股列表（格式同深圳股）
    - 后 1,172,034 × 11 字节：财务数据（每股票 446 条记录）

文件结构（gpcw20260331.dat）：
  [Header: 31854 bytes]
    [深圳股列表: offset 20, 2894 × 11 bytes]
  [Data Section]
    [上海股列表: 2625 × 11 bytes, 代码范围 600000~603xxx]
    [财务数据: 1172034 × 11 bytes, 每股票 446 条]

  Stock list entry (11 bytes):
    字节 0-5: 6字节 ASCII 股票代码
    字节 6-10: 5字节指针（未解析，直接通过结构计算偏移）

  数据记录格式（11字节）：
    字节 0-2: 3字节小端整数 (value_main)
    字节 3-4: 2字节小端整数 (value_aux)
    字节 5-10: 未使用

  注意：字段具体含义（收入/利润/EPS等）尚未解码。

文件示例:
  gpcw20260331.dat - 2026-03-31 版本
    - 上海股: 2625 只
    - 深圳股: 2894 只
    - 总计: 5519 只
    - 每股票记录: 446
    - 总记录: 1174659

Usage:
  from lib.data_tdx_fin import GpcwReader
  reader = GpcwReader()
  df = reader.read_stock("600519")  # 茅台（上海）
  df = reader.read_stock("000001")  # 平安（深圳）
"""
import struct
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ========== 配置 ==========
TDX_FIN_PATH = Path("D:/TDX/vipdoc/cw")

# ========== gpcw 格式定义 ==========
HEADER_SIZE = 31854
RECORD_SIZE = 11


class GpcwReader:
    """gpcw*.dat 财务报表读取器

    文件结构：
      - Header (31854 bytes): 深圳股列表在 offset 20
      - Data section:
        - 前 2625 条：上海股列表
        - 后 1,172,034 条：财务数据（每股票 446 条）

    支持上海/深圳/创业板/科创板所有A股。
    """

    def __init__(self, data_path: Optional[str] = None):
        self.data_path = Path(data_path) if data_path else TDX_FIN_PATH
        self._cache: Dict[str, pd.DataFrame] = {}
        self._file_meta_cache: Dict[str, Dict] = {}

    def _get_latest_file(self) -> Optional[Path]:
        """获取最新的有效 gpcw*.dat 文件（按日期排序）"""
        files = list(self.data_path.glob("gpcw*.dat"))
        if not files:
            return None

        def parse_date(f):
            try:
                return f.stem[4:]
            except Exception as e:
                logger.debug(f"[tdx_fin] 文件日期解析失败: {e}")
                return ""

        valid_files = [f for f in files if f.stat().st_size > HEADER_SIZE]
        if not valid_files:
            logger.warning("cw/ 目录下没有找到有效的 gpcw*.dat 文件")
            return None
        valid_files.sort(key=parse_date, reverse=True)
        return valid_files[0]

    def get_all_period_files(self) -> list:
        """获取所有有效的 gpcw*.dat 文件（按日期排序，最新的在前）"""
        files = list(self.data_path.glob("gpcw*.dat"))
        valid_files = [f for f in files if f.stat().st_size > HEADER_SIZE]
        valid_files.sort(key=lambda f: f.stem[4:] if len(f.stem) > 4 else "", reverse=True)
        return valid_files

    def _analyze_file(self, filepath: Path) -> Dict:
        """分析文件结构，返回元数据

        正确结构（已验证）：
          - Header (31854 bytes): 深圳股列表在 offset 20，11字节/条
          - Data section: 前2625条记录是上海股列表，后1,172,034条是财务数据
          - 每只股票固定446条财务记录
        """
        if filepath.name in self._file_meta_cache:
            return self._file_meta_cache[filepath.name]

        with open(filepath, "rb") as f:
            header = f.read(HEADER_SIZE)
            data = f.read()

        total_data_records = len(data) // RECORD_SIZE

        # === 深圳股：从 header offset 20 读取 ===
        shenzhen_stocks = []
        offset = 20
        while offset + RECORD_SIZE <= HEADER_SIZE:
            rec = header[offset:offset + RECORD_SIZE]
            code = rec[0:6].decode("ascii", errors="replace")
            if code.isdigit() and len(code) == 6:
                shenzhen_stocks.append(code)
                offset += RECORD_SIZE
            else:
                break

        # === 上海股：从 data section 前2625条读取 ===
        shanghai_stocks = []
        for i in range(2625):
            rec = data[i * RECORD_SIZE:(i + 1) * RECORD_SIZE]
            code = rec[0:6].decode("ascii", errors="replace")
            if code.isdigit():
                shanghai_stocks.append(code)
            else:
                break

        # === 财务数据记录 ===
        financial_records = total_data_records - len(shanghai_stocks)
        recs_per_stock = financial_records // len(shanghai_stocks) if shanghai_stocks else 0

        meta = {
            "shanghai_stocks": shanghai_stocks,
            "shenzhen_stocks": shenzhen_stocks,
            "stock_list": shanghai_stocks + shenzhen_stocks,  # 兼容旧接口
            "stock_count": len(shanghai_stocks) + len(shenzhen_stocks),
            "shanghai_count": len(shanghai_stocks),
            "shenzhen_count": len(shenzhen_stocks),
            "total_records": total_data_records,
            "data_records": financial_records,
            "recs_per_stock": recs_per_stock,
        }

        self._file_meta_cache[filepath.name] = meta
        return meta

    def _read_file_data(self, filepath: Path) -> bytes:
        """读取文件数据部分（跳过 header）"""
        with open(filepath, "rb") as f:
            f.read(HEADER_SIZE)
            return f.read()

    def read_all_codes(self) -> List[str]:
        """读取所有股票代码列表"""
        latest_file = self._get_latest_file()
        if not latest_file:
            return []
        meta = self._analyze_file(latest_file)
        return meta["stock_list"]

    def read_stock(self, code: str, filepath: Optional[Path] = None) -> pd.DataFrame:
        """读取指定股票的所有财务数据记录

        Args:
            code: 6位股票代码，如 "600519"（贵州茅台）
            filepath: 可选，指定 gpcw 文件路径（默认使用最新文件）

        Returns:
            DataFrame with columns:
              - field_index: 字段索引
              - value_main: 3字节主值
              - value_aux: 2字节辅助值
              - raw_bytes: 原始5字节数据（hex）
        """
        code = code.strip().zfill(6)
        if filepath is None:
            filepath = self._get_latest_file()
        if not filepath:
            logger.warning("找不到 gpcw*.dat 文件")
            return pd.DataFrame()

        cache_key = f"{filepath.name}:{code}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        meta = self._analyze_file(filepath)
        shanghai_stocks = meta["shanghai_stocks"]
        shenzhen_stocks = meta["shenzhen_stocks"]
        recs_per_stock = meta["recs_per_stock"]

        # 判断上海还是深圳
        # 上海股：数据 record = 2625 + sh_idx * 446
        # 深圳股：数据 record = 1171275 + sz_idx * 446
        # 注意：深圳股数据仅覆盖 sz_idx 0~7 左右（受文件大小限制）
        if code in shanghai_stocks:
            sh_idx = shanghai_stocks.index(code)
            data_start = 2625 + sh_idx * recs_per_stock
        elif code in shenzhen_stocks:
            sz_idx = shenzhen_stocks.index(code)
            data_start = 1171275 + sz_idx * recs_per_stock
        else:
            logger.debug(f"股票 {code} 不在股票列表中")
            return pd.DataFrame()

        data = self._read_file_data(filepath)
        total_data_records = len(data) // RECORD_SIZE
        if data_start >= total_data_records:
            logger.debug(f"股票 {code} 数据位置 {data_start} 超出文件范围 {total_data_records}")
            return pd.DataFrame()
        rows = []
        for field_idx in range(recs_per_stock):
            rec_idx = data_start + field_idx
            byte_offset = rec_idx * RECORD_SIZE
            if byte_offset + RECORD_SIZE > len(data):
                break

            rec = data[byte_offset:byte_offset + RECORD_SIZE]
            data_bytes = rec[0:5]

            if data_bytes == b'\x00\x00\x00\x00\x00':
                continue

            val_main = int.from_bytes(data_bytes[0:3], "little")
            val_aux = struct.unpack("<H", data_bytes[3:5])[0]

            rows.append({
                "field_index": field_idx,
                "value_main": val_main,
                "value_aux": val_aux,
                "raw_bytes": data_bytes.hex(),
            })

        df = pd.DataFrame(rows)
        self._cache[cache_key] = df
        return df

    def get_file_info(self, filepath: Optional[Path] = None) -> Dict:
        """获取 gpcw 文件信息"""
        if filepath is None:
            filepath = self._get_latest_file()
        if not filepath or not filepath.exists():
            return {}

        meta = self._analyze_file(filepath)

        with open(filepath, "rb") as f:
            header = f.read(HEADER_SIZE)

        magic = struct.unpack("<I", header[0:4])[0]
        val4 = struct.unpack("<I", header[4:8])[0]
        val12 = struct.unpack("<H", header[12:14])[0]

        return {
            "filename": filepath.name,
            "size_bytes": filepath.stat().st_size,
            "header_size": HEADER_SIZE,
            "total_records": meta["total_records"],
            "shanghai_count": meta["shanghai_count"],
            "shenzhen_count": meta["shenzhen_count"],
            "stock_count": meta["stock_count"],
            "data_records": meta["data_records"],
            "recs_per_stock": meta["recs_per_stock"],
            "magic": f"0x{magic:08X}",
            "header_val4": f"0x{val4:08X}",
            "header_val12": f"0x{val12:04X}",
        }


def get_stock_financial(code: str) -> pd.DataFrame:
    """读取股票财务数据的便捷函数"""
    reader = GpcwReader()
    return reader.read_stock(code)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    reader = GpcwReader()

    info = reader.get_file_info()
    print("=== gpcw 文件信息 ===")
    for k, v in info.items():
        print(f"  {k}: {v}")

    codes = reader.read_all_codes()
    print(f"\n=== 股票代码总数: {len(codes)} ===")
    print(f"  前10个: {codes[:10]}")
    print(f"  后10个: {codes[-10:]}")

    for code in ["600519", "600000", "600036"]:
        df = reader.read_stock(code)
        if not df.empty:
            print(f"\n{code}: {len(df)} 条非空记录")
            if len(df) > 0:
                print(f"  Field 0: main={df.iloc[0]['value_main']}, aux={df.iloc[0]['value_aux']}")
        else:
            print(f"\n{code}: 无数据")
