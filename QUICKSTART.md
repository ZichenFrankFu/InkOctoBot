# 快捷CLI启动指南
## 1. 切换环境
```bash
conda activate InkOctoBot
```
## 2. 安装依赖
```bash
pip install -r requirements.txt
```

## 3. Spider
### 3.1. 一键启动，爬取起点 + 番茄，全部榜单
```bash
python main.py once
```
### 3.2. 只抓取某个平台 + 某个榜单
```bash
python main.py once --platform qidian --rank_key 月票榜 --qidian_pages 5 --chapter_count 5
python main.py once --platform fanqie --rank_key 新书榜科幻末世 --newbook_chapter_count 2
python main.py once --platform fanqie --rank_key 阅读榜玄幻脑洞 --chapter_count 5
```
### 3.3. 只抓某个平台（跑该平台所有榜单）
```bash
python main.py once --platform qidian --qidian_pages 2
python main.py once --platform fanqie --chapter_count 5 --newbook_chapter_count 2
```

## 4. Spider Test
### 4.1. qidian_test
#### 4.1.1 快速测试（抓取一个榜单的第一本小说，获取该小说的 metadata + 第一章，不写库，用于快速验证 HTML 结构是否发生变化）
```bash 
python tests/qidian_test.py --test quick --rank_key "月票榜"
```
#### 4.1.2 完整测试（抓取一个榜单的前三本小说及其 metadata，抓取每本小说前5章正文，写入测试数据库）
```bash 
python tests/qidian_test.py --test full --rank_key "月票榜"
```
#### 4.1.3 测试多个榜单（按多个榜单循环抓取，默认每榜单抓1本小说，并保存每本小说前3章正文）
```bash
python tests/qidian_test.py --test multi_ranks --rank_keys "月票榜,畅销榜,推荐榜"
```
#### 4.1.4 智能补全测试（只测试抓取，不写入数据库）
```bash
python tests/qidian_test.py --test smart_fetch --rank_key "月票榜" --pages 1 --chapter_n1 3 --chapter_n2 5
```

### 4.2. fanqie_test
#### 4.2.1 测试反爬字体解密（仅番茄）
```bash
python tests/fanqie_test.py --test decryption
```
#### 4.2.2 快速测试（抓取一个榜单的第一本小说，获取该小说的 metadata + 第一章，不写库，用于快速验证 HTML 结构是否发生变化）
```bash
python tests/fanqie_test.py --test quick --rank_key "阅读榜科幻末世"
```
#### 4.2.3 完整测试（顺序执行所有测试类型, 覆盖：榜单、详情、章节、智能补全、去重、字体解密、多榜单, 写入测试数据库）
```bash
python tests/fanqie_test.py --test full --rank_key "阅读榜科幻末世"
```
#### 4.2.4 测试多个榜单
```bash
python tests/fanqie_test.py --test multi_ranks --rank_keys "阅读榜西方奇幻,阅读榜科幻末世,新书榜西方奇幻"
```
#### 4.2.5 智能补全测试（只测试抓取，不写入数据库）
```bash
python tests/fanqie_test.py --test smart_fetch --rank_key "阅读榜西方奇幻" --pages 1 --chapter_n1 3 --chapter_n2 5
```
#### 4.2.6 小说改名测试
```bash
python tests/fanqie_test.py --test fake_rename
```

## 5. Analysis 
```bash
python analysis/run_analysis.py --db ../outputs/data/novels.db --platform both --top_k 10 --lookback all
```