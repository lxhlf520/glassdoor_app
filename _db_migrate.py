"""MongoDB 数据迁移：雇主列表 + 采集进度导出/导入。

用法:
  源机器:  python _db_migrate.py --export [-o ./export/]
  目标机:  python _db_migrate.py --import [-i ./export/]

环境变量:
  MONGO_URI          MongoDB 连接串 (默认 mongodb://localhost:27017)
  MONGO_URI_TARGET   导入目标 MongoDB (可选，默认与 MONGO_URI 相同)
"""
import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB = "glassdoor"
COLL_EMPLOYERS = "app_employers"
COLL_PROGRESS = "app_review_progress"


def _serialize_doc(doc: dict) -> dict:
    """将 MongoDB 文档转为 JSON-safe dict（处理 ObjectId/datetime）。"""
    out = {}
    for k, v in doc.items():
        if k == "_id":
            out[k] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, (bytes,)):
            out[k] = v.hex()
        else:
            out[k] = v
    return out


def export_data(output_dir: str):
    client = MongoClient(MONGO_URI)
    db = client[DB]
    os.makedirs(output_dir, exist_ok=True)

    def _export(coll_name, filename):
        docs = list(db[coll_name].find())
        rows = [_serialize_doc(d) for d in docs]
        path = os.path.join(output_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        print(f"  {coll_name}: {len(rows)} docs → {path}")

    _export(COLL_EMPLOYERS, "app_employers.json")
    _export(COLL_PROGRESS, "app_review_progress.json")
    print(f"\n导出完成，目录: {output_dir}")


def import_data(input_dir: str):
    target_uri = os.environ.get("MONGO_URI_TARGET", MONGO_URI)
    client = MongoClient(target_uri)
    db = client[DB]

    def _import(coll_name, filename):
        path = os.path.join(input_dir, filename)
        if not os.path.exists(path):
            print(f"  SKIP {coll_name}: 文件 {filename} 不存在")
            return
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        if not rows:
            print(f"  SKIP {coll_name}: 空文件")
            return
        existing = db[coll_name].count_documents({})
        if existing > 0:
            print(f"  WARN {coll_name}: 已有 {existing} 条，跳过导入（如需覆盖请先 drop）")
            return
        # 批量插入
        from pymongo import InsertOne
        from pymongo.errors import BulkWriteError
        ops = [InsertOne(doc) for doc in rows]
        try:
            db[coll_name].bulk_write(ops, ordered=False)
        except BulkWriteError as bwe:
            pass  # 部分重复忽略
        imported = db[coll_name].count_documents({})
        print(f"  {coll_name}: {imported} docs ← {path}")

    _import(COLL_EMPLOYERS, "app_employers.json")
    _import(COLL_PROGRESS, "app_review_progress.json")
    print(f"\n导入完成，目标: {target_uri}")


def main():
    p = argparse.ArgumentParser(description="Glassdoor MongoDB 迁移工具")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true")
    g.add_argument("--import", action="store_true", dest="import_")
    p.add_argument("-o", "--output", default="./export/", help="导出目录")
    p.add_argument("-i", "--input", default="./export/", help="导入目录")
    args = p.parse_args()

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        client.server_info()
    except Exception as e:
        if args.export:
            print(f"ERROR: 无法连接 MongoDB ({MONGO_URI}): {e}")
            sys.exit(1)

    if args.export:
        export_data(args.output)
    else:
        import_data(args.input)


if __name__ == "__main__":
    main()
