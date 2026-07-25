"""快速测试采集器：2家公司各少量页"""
import logging

from collector import GlassdoorCollector

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

c = GlassdoorCollector()

# 清空测试数据
c.reviews.delete_many({})

# 测试: Apple 前2页
print("\n=== Apple page 1-2 ===")
n = c._collect_employer_reviews(1138, 2, "Apple Inc.")
print(f"  Inserted: {n}")

# 测试: DeepMind 前2页
print("\n=== DeepMind page 1-2 ===")
n = c._collect_employer_reviews(1596815, 2, "DeepMind")
print(f"  Inserted: {n}")

# 验证
apple_count = c.reviews.count_documents({"employerId": 1138})
dm_count = c.reviews.count_documents({"employerId": 1596815})
print(f"\nTotal in DB: Apple={apple_count}, DeepMind={dm_count}")

# 显示一条记录
doc = c.reviews.find_one({"employerId": 1138})
if doc:
    print(f"\nSample review: [{doc.get('ratingOverall')}*] {doc.get('summary','')[:80]}")
    print(f"  Pros: {doc.get('pros','')[:80]}")
    print(f"  Cons: {doc.get('cons','')[:80]}")
