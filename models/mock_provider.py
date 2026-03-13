"""
Mock LLM provider for test mode.

Returns context-aware, deterministic responses so ALL features can be tested
without a real AI model connection. Covers:
- Outline brainstorming (头脑风暴 / outline-chat)
- Style calibration (风格校准)
- Quick generation / creative writing
- Creative writing pipeline (SceneDirector → Actors → Editor → Evaluator)
- Evaluation (评估)
- Character AI assistant (AI角色助手)
- World book consistency check (世界书一致性检查)
"""
from __future__ import annotations

import json
import random
from typing import Any, AsyncIterator

from models.base import BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig


# ═══ Response templates by feature ═══

_OUTLINE_BRAINSTORM = (
    "这个构思很有潜力！我来帮你梳理一下思路：\n\n"
    "## 核心冲突\n"
    "主角在星际考古中发现远古文明的秘密，触发了一系列连锁反应——"
    "神秘组织的追杀、政府的封锁令、以及远古防御系统的苏醒。\n\n"
    "## 建议的故事结构\n"
    "**第一幕 · 发现**（第1-3章）\n"
    "- 废弃矿星上的意外发现\n"
    "- 联邦特工介入调查\n"
    "- 主角被迫卷入更大的阴谋\n\n"
    "**第二幕 · 追寻**（第4-7章）\n"
    "- 与特工联手追寻星门线索\n"
    "- 发现远古文明灭亡的真相\n"
    "- 反派势力浮出水面\n\n"
    "**第三幕 · 抉择**（第8-10章）\n"
    "- 星门激活的代价\n"
    "- 主角面临终极选择\n"
    "- 意想不到的结局反转\n\n"
    "## 几个关键问题值得思考\n"
    "1. 主角的「感知能力」有什么代价？每次使用是否会付出代价？\n"
    "2. 远古文明为什么灭亡？这与主线冲突有什么关联？\n"
    "3. 主角和女主之间的信任是如何建立的？需要设计3-4个关键场景。\n\n"
    "你想先深入哪个方向？我可以帮你细化任何一个部分。"
)

_OUTLINE_FOLLOWUP = (
    "好的，让我在你的基础上继续发展：\n\n"
    "关于这个方向，我有几点具体建议：\n\n"
    "1. **角色动机**：可以设定主角最初只是出于学术好奇，但随着发现的深入，"
    "他意识到远古文明的遗产关乎整个银河的命运。这种从「小目标」到「大使命」的转变"
    "会让读者更有代入感。\n\n"
    "2. **情节节拍**：建议在每章末尾设置一个小悬念（hook），例如：\n"
    "   - 第一章末：水晶中出现了一张星图\n"
    "   - 第二章末：特工发现有人在监控他们\n"
    "   - 第三章末：遗迹深处传来不属于任何已知文明的声音\n\n"
    "3. **世界观细节**：星门的运作原理可以和主角的感知能力相关联——"
    "也许星门需要「共鸣者」才能激活，而主角恰好具备这种天赋。\n\n"
    "要不要我把这些整理成一个更详细的章节大纲？"
)

_CALIBRATION_SAMPLE = (
    "夜幕低垂，星光如碎银般洒落在废弃的矿星表面。\n\n"
    "李星河蹲在一处裸露的岩层前，手中的便携式光谱仪发出微弱的蓝色光芒。"
    "他的目光紧紧锁住岩层深处那块半透明的水晶——它的内部似乎有什么东西在流动，"
    "像是被困住的星光，又像是凝固的时间。\n\n"
    "\"奇怪……\"他喃喃自语，指尖轻轻触上水晶的表面。\n\n"
    "一瞬间，无数画面如潮水般涌入脑海——那是亿万年前的记忆残片。"
    "他看到了巨大的环形建筑在星空下缓缓旋转，看到了身着银色长袍的身影"
    "在操控着某种超越人类理解的能量。他感受到了恐惧、不甘，"
    "以及一种超越时空的、深入骨髓的孤独。\n\n"
    "然后，一切戛然而止。\n\n"
    "李星河猛地抽回手，大口喘着气。额头上渗出的冷汗在夜风中迅速蒸发，"
    "留下一阵刺骨的凉意。他低头看着自己微微颤抖的手指——每一次使用这种能力，"
    "都像是在窥视深渊。\n\n"
    "而深渊，也在凝视着他。"
)

_CHARACTER_FULL_PROFILE = (
    '{"personality": "外表沉稳冷静，实则内心充满对未知的好奇与热情。'
    '面对危险时会本能地保护同伴，但不善于表达自己的感情。'
    '有着学者特有的执着——一旦遇到感兴趣的课题，会完全沉浸其中，忘记时间和危险。'
    '内心深处因为特殊能力而感到与常人格格不入，渴望被理解却又害怕被当作异类。", '
    '"background": "出身于联邦边缘星系的普通矿工家庭。父亲是退休的勘探工人，'
    '母亲在当地学校教书。童年时在一次矿难中意外接触到远古遗物，'
    '获得了感知古代文明情感残留的特殊能力。这种能力让他在考古学界崭露头角，'
    '以全额奖学金进入银河联邦大学考古学系。研究生期间发表了三篇有影响力的论文，'
    '被导师称为「百年难遇的天才」。然而，只有他自己知道，'
    '每次使用能力后那种被异世记忆侵蚀的痛苦有多难以承受。", '
    '"speech_style": "用词简洁精准，习惯使用考古学专业术语。'
    '语速不快，但逻辑清晰。紧张时会不自觉地摸后脑勺或推一下不存在的眼镜框。'
    '偶尔会冒出带有矿区方言味道的口头禅「矿渣」来骂人。'
    '面对同龄人比较随和，但在学术讨论中会变得一丝不苟。"}'
)

_CHARACTER_CHAT = (
    "根据你的需求，我为这个角色补充以下设定：\n\n"
    "**性格矛盾点**：表面上的冷静自持与内心深处对被理解的渴望形成张力。"
    "这种矛盾可以在关键场景中被激化——比如当有人真正理解他的能力时，"
    "他的情感防线会瞬间崩塌。\n\n"
    "**标志性行为**：\n"
    "- 思考时喜欢把玩一枚来自家乡的矿石吊坠\n"
    "- 使用能力前会深呼一口气，像是在做心理准备\n"
    "- 面对危险时反而会变得格外平静（应激反应）\n\n"
    "**成长弧线建议**：\n"
    "1. 初期：隐藏能力，独来独往\n"
    "2. 中期：被迫展示能力，面对他人的恐惧和利用\n"
    "3. 后期：接受自己的独特性，找到真正的同伴\n\n"
    "这些设定和你现有的角色框架应该很契合。需要我进一步细化哪个方面？"
)

_CONSISTENCY_CHECK = (
    "经过仔细检查，发现以下潜在问题：\n\n"
    "1. 银河联邦的建立时间在「社会结构」条目中为2500年，"
    "但在星门条目的描述中暗示联邦科学家已经研究星门「数百年」，"
    "这意味着星门的发现时间可能需要确认是否早于联邦成立。\n\n"
    "2. 星门被描述为「均处于休眠状态」，但如果主角通过感知能力发现了激活线索，"
    "建议增加一个条目说明为什么之前没有人尝试过类似的方法。\n\n"
    "3. 整体设定一致性良好。修炼/能力体系和社会结构之间没有明显矛盾。\n\n"
    "建议：可以增加一个关于「远古文明」的专门条目，明确其技术水平和灭亡原因，"
    "以避免后续写作中出现设定冲突。"
)

_SCENE_PLAN_JSON = json.dumps({
    "chapter_num": 1,
    "chapter_arc": "从平静的考古调查 → 神秘发现带来的震撼 → 危险逼近的紧张感",
    "scenes": [
        {
            "scene_index": 0,
            "location": "废弃矿星K-7·地表",
            "time": "银河历2847年·秋·黄昏",
            "characters": ["李星河"],
            "summary": "李星河在矿星地表进行例行考古调查，发现异常的水晶矿脉",
            "beats": [
                "描写矿星荒凉的地表环境，营造孤独感",
                "光谱仪检测到异常信号",
                "发现半透明的远古水晶",
                "犹豫是否使用感知能力"
            ],
            "character_instructions": {
                "李星河": {
                    "emotional_state": "好奇但谨慎",
                    "secret_goal": "证明自己的学术价值",
                    "must": ["展示专业的考古操作流程", "内心独白暗示对能力的恐惧"],
                    "must_not": ["不要过早展示全部能力", "不要提及其他章节的角色"]
                }
            }
        },
        {
            "scene_index": 1,
            "location": "废弃矿星K-7·矿洞入口",
            "time": "银河历2847年·秋·深夜",
            "characters": ["李星河"],
            "summary": "李星河使用感知能力触碰水晶，获得远古文明的记忆碎片",
            "beats": [
                "鼓起勇气使用感知能力",
                "远古文明的画面涌入脑海",
                "感知到星门的位置信息",
                "能力反噬带来的痛苦",
                "章末悬念：远处传来飞船引擎的轰鸣"
            ],
            "character_instructions": {
                "李星河": {
                    "emotional_state": "从好奇到震撼再到恐惧",
                    "secret_goal": "理解远古文明的秘密",
                    "must": ["详细描写感知过程的体验", "暗示这次感知的信息量远超以往"],
                    "must_not": ["不要完全揭示星门的秘密"]
                }
            }
        }
    ]
}, ensure_ascii=False)

_EVALUATION_JSON = json.dumps({
    "passed": True,
    "score": 82,
    "dimension_scores": {
        "constraint_satisfaction": 85,
        "consistency": 88,
        "knowledge_isolation": 90,
        "repetition": 78,
        "ai_flavor": 75,
        "narrative_quality": 85
    },
    "issues": [
        {
            "type": "repetition",
            "severity": "medium",
            "description": "「星光」一词在前三段中重复出现了4次，建议替换部分用词",
            "suggestion": "可以用「光芒」「辉光」「微光」等近义词交替使用"
        },
        {
            "type": "ai_flavor",
            "severity": "low",
            "description": "第二段中「无数画面如潮水般涌入脑海」略显俗套",
            "suggestion": "尝试更具个人特色的比喻，如「记忆像碎裂的全息影像一帧帧闪过」"
        }
    ],
    "strengths": [
        "环境描写有画面感，成功营造了矿星的荒凉氛围",
        "主角的内心活动描写细腻，展现了能力使用时的矛盾心理",
        "章末设置了有效的悬念，引导读者继续阅读"
    ],
    "summary_text": "整体质量良好，叙事节奏合理。主要需要注意用词重复和部分表达的原创性。"
}, ensure_ascii=False)

_QUICK_GENERATE = (
    "银河联邦历2847年，人类的星际版图已延伸至猎户座旋臂的边缘。\n\n"
    "在那些被遗忘的星域中，散落着无数远古文明的遗迹。"
    "它们沉默地矗立在荒凉的星球表面，如同一个个未解的谜题，"
    "等待着被发现、被解读、被唤醒。\n\n"
    "李星河把光谱仪塞进背包侧袋，站起身来环顾四周。"
    "矿星K-7的地表是一片无尽的灰色荒原，稀薄的大气在低矮的山脊上扯出几缕幽蓝的光带。"
    "联邦气象台的公报说，这里的昼夜温差超过一百七十度，"
    "但他穿着标准的考古学院野外夹克，内置的恒温系统让他感觉还算舒适。\n\n"
    "\"编号K-7-041号样本，光谱特征异常。\"他对着记录仪低声报告，"
    "\"初步判断为非已知矿物组成。表面有规律性微结构，疑似人工制品。\"\n\n"
    "他停顿了一下，看着手中那块半透明的水晶。它的内部似乎有什么东西在缓慢流动，"
    "像是被困住的星光——不，更像是凝固的时间本身。\n\n"
    "李星河深吸一口气。他知道自己不该这么做。每次使用那种能力，"
    "都会在他的意识深处留下一道新的裂痕。但他也知道，如果不触碰这块水晶，"
    "他可能永远无法知道它隐藏着什么样的秘密。\n\n"
    "指尖触上冰凉的水晶表面的那一刻，世界安静了。\n\n"
    "然后，亿万年前的记忆，如洪流般将他淹没。"
)

_PIPELINE_ACTOR_PROSE = (
    "李星河\n"
    "（神色警觉，突然停下脚步，侧耳倾听矿洞深处传来的低沉嗡鸣）\n"
    "他下意识地屏住呼吸，手指微微收紧。那声音很低，低到几乎和矿星自转产生的地质噪音混在一起。\n\n"
    "          李星河\n"
    "    \"你听到了吗？那个频率……太规律了，不像是自然现象。\"\n\n"
    "（内心：这个方向，和昨天水晶给我看到的星门图像——方位完全一致。如果我没有判断失误，星门就在前方三百米左右。）\n\n"
    "李星河指着矿洞深处的黑暗，目光中带着一丝难以掩饰的兴奋。\n\n"
    "          李星河\n"
    "    \"在前面，大约三百米。那个方向……和水晶里看到的星门方位一致。\"\n\n"
    "（内心：她会相信我吗？自从两天前被迫组队以来，这位联邦特工一直对我保持着职业性的冷淡。"
    "但现在我需要她的支持——一个人闯进去太冒险了。）\n\n"
    "他注意到苏晚的眼神变了，那种审视的目光消失了，取而代之的是某种他说不清的东西。\n"
    "李星河看着她一言不发地走在前面的背影，心里涌起一种奇怪的感觉——"
    "也许在这片荒凉的星域中，他终于不再是孤身一人了。"
)

_EDITOR_REWRITE = (
    "矿洞深处传来的低沉嗡鸣让李星河如遭雷击般僵在原地。\n\n"
    "苏晚第一时间察觉到异样。她的右手以一种训练有素的弧度滑向腰间，"
    "指尖轻触粒子枪的握把——这个动作她做过上千次，快到连她自己都不需要思考。\n\n"
    "\"说。\"一个字，干脆利落，没有多余的音节。\n\n"
    "\"那个声音——\"李星河侧着头，屏住呼吸。"
    "在寂静中，那个频率变得更加清晰：恒定的、有节奏的、绝不可能是自然产生的脉冲。"
    "像是某种东西的心跳。或者，像是某种东西在呼唤。\n\n"
    "\"三百米，正前方。和水晶里那个星门的方位——完全吻合。\"\n\n"
    "苏晚沉默了两秒。然后她做了一件出乎李星河意料的事——"
    "她关掉了安全锁，走在了前面。\n\n"
    "没有质疑，没有犹豫，没有那种他已经习惯的、来自正常人的审视目光。"
    "在这片荒凉星域的黑暗深处，信任第一次以最朴素的方式出现：一个人把后背交给了另一个人。\n\n"
    "李星河看着她的背影，感到胸口某个一直绷紧的东西，悄悄松了下来。"
    "他加快脚步跟了上去，手中的矿灯在粗糙的岩壁上投下两道交织的光影，"
    "一前一后，向着矿洞深处延伸。"
)

_AUTO_OUTLINE = (
    '{"outline": ['
    '{"chapter_num": 1, "title": "意外发现", "synopsis": "李星河在废弃矿星K-7的例行调查中，发现了一块异常的远古水晶。使用感知能力后，他获得了关于「星门」位置的关键信息。", "key_events": ["发现异常水晶", "使用感知能力", "获得星门线索"], "characters": ["李星河"]},'
    '{"chapter_num": 2, "title": "不速之客", "synopsis": "联邦安全局特工苏晚带队突然降落矿星，调查频繁出现的远古信号源。李星河的发现引起了她的高度关注。", "key_events": ["苏晚出场", "身份冲突", "被迫合作"], "characters": ["李星河", "苏晚"]},'
    '{"chapter_num": 3, "title": "遗迹深处", "synopsis": "李星河被征用为调查顾问，与苏晚深入矿星地下遗迹。两人在共同面对古代防御机制时，开始建立信任。", "key_events": ["深入遗迹", "防御机制", "初步信任"], "characters": ["李星河", "苏晚"]}'
    ']}'
)

_BRAINSTORM_JSON = json.dumps({
    "answer": (
        "这个构思很有潜力！星际考古+感知能力的设定很有新意。"
        "我帮你梳理一下核心要素：\n\n"
        "**核心冲突**：主角的感知能力既是解开远古秘密的钥匙，也是引来各方势力的原因。\n\n"
        "**核心悬念**：远古文明为何灭亡？星门激活会带来什么后果？\n\n"
        "**情感线索**：主角从「被迫合作」到「相互信任」的关系演变。\n\n"
        "建议先确定以下几个关键设定，再展开详细大纲。"
    ),
    "follow_up": [
        {
            "text": "主角的感知能力应该有什么样的代价或限制？",
            "options": [
                "每次使用后会短暂失去自己的记忆",
                "使用越多，被远古意识侵蚀的风险越大",
                "只能在特定条件下触发（如接触特定材质）",
                "能力不稳定，有时会接收到错误信息"
            ]
        },
        {
            "text": "你希望故事的整体基调偏向哪个方向？",
            "options": [
                "硬科幻探险 — 注重世界观逻辑和科学设定",
                "太空歌剧 — 宏大叙事+热血战斗",
                "悬疑解谜 — 层层揭秘远古文明的真相"
            ]
        }
    ]
}, ensure_ascii=False)

_CALIBRATION_FEEDBACK_JSON = json.dumps({
    "text": (
        "矿洞的尽头，是一片出乎意料的开阔空间。\n\n"
        "穹顶高达百米，表面密布着螺旋状的纹路，在头灯的照射下折射出幽蓝色的微光。"
        "这些纹路不是天然形成的——它们太规律、太精确，带着一种超越人类审美的冷峻几何美感。\n\n"
        "\"这是……\"李星河喉咙发紧，声音不自觉地压低了，仿佛怕惊醒沉睡在这里的什么东西。\n\n"
        "苏晚没有回答。她关掉了头灯的白光模式，切换到红外。"
        "在热成像视野中，这个空间呈现出完全不同的面貌：穹顶上的那些纹路，竟然还在缓慢地流动，"
        "散发着比环境温度高出整整三度的微弱热量。\n\n"
        "\"还活着。\"苏晚的声音很轻，但李星河听出了其中压抑的震惊，\"这些纹路——或者说这个结构——"
        "它还在运转。亿万年了，它还在运转。\"\n\n"
        "李星河缓缓走向穹顶正下方的一块巨大水晶平台。他的感知能力在尖叫，"
        "前所未有地强烈，像是有什么东西在主动呼唤他。"
    ),
    "analysis": (
        "根据用户上一轮的反馈（希望节奏更紧凑、增加悬念感），本次调整：\n"
        "1. 缩短环境描写段落，每段不超过3句\n"
        "2. 用角色反应来推动节奏，而非单纯描写\n"
        "3. 在末尾增加了「主动呼唤」的悬念元素\n"
        "4. 加入了更多角色互动（苏晚的专业视角补充）"
    )
}, ensure_ascii=False)

_CHARACTER_GENERATE_PROFILE_JSON = json.dumps({
    "personality": (
        "外表沉稳冷静，实则内心充满对未知的好奇与热情。"
        "面对危险时会本能地保护同伴，但不善于表达自己的感情。"
        "有着学者特有的执着——一旦遇到感兴趣的课题，会完全沉浸其中。"
        "内心深处因特殊能力而感到与常人格格不入，渴望被理解却又害怕被当作异类。"
    ),
    "background": (
        "出身于联邦边缘星系的普通矿工家庭。童年时在一次矿难中意外接触到远古遗物，"
        "获得了感知古代文明情感残留的特殊能力。以全额奖学金进入银河联邦大学考古学系，"
        "研究生期间发表了三篇有影响力的论文。只有他自己知道每次使用能力后的痛苦。"
    ),
    "speech_style": (
        "用词简洁精准，习惯使用考古学专业术语。语速不快但逻辑清晰。"
        "紧张时会不自觉地摸后脑勺。偶尔会冒出带有矿区方言味道的口头禅。"
    ),
    "appearance": "黑发微乱，深邃的棕色眼睛带着学者特有的专注。中等身材偏瘦，常穿旧款考古学院野外夹克。",
    "tags": ["考古学者", "感知能力", "内向", "执着", "矿工家庭"]
}, ensure_ascii=False)

_AB_COMPARE_RESULT = (
    '{"comparison": {'
    '"version_a_score": 78, "version_b_score": 85,'
    '"analysis": "B版本在节奏把控和情感张力方面优于A版本。A版本的环境描写更为细致，但整体叙事略显拖沓。",'
    '"recommendation": "B",'
    '"details": {'
    '"narrative_quality": {"a": 75, "b": 88, "note": "B版本的场景转换更加流畅"},'
    '"character_voice": {"a": 80, "b": 82, "note": "两个版本角色voice都不错，B略胜一筹"},'
    '"pacing": {"a": 72, "b": 85, "note": "A版本中间段落节奏偏慢"}'
    '}}}'
)

_PROACTIVE_QUESTION = (
    "在审阅你目前的大纲和角色设定后，我发现几个值得深入思考的问题：\n\n"
    "**1. 关于主角能力的边界**\n"
    "李星河的感知能力目前没有明确的使用限制。如果不加以约束，可能会在后续情节中导致"
    "「万能钥匙」问题——任何难题都能通过感知来解决。\n"
    "- 你打算给这种能力设定什么样的代价或冷却机制？\n"
    "- 是否有某些类型的信息是他无法感知的？\n\n"
    "**2. 反派势力的动机**\n"
    "目前大纲中提到了「神秘组织」，但他们追求星门的具体目的还不明确。\n"
    "- 他们想用星门做什么？征服？回到过去？还是有更深层的原因？\n"
    "- 反派是否也有合理的立场（灰色地带），还是纯粹的对立？\n\n"
    "**3. 角色关系发展节奏**\n"
    "李星河和苏晚从「被迫合作」到「建立信任」的转变目前在第三章就完成了，"
    "这是否太快？建议：\n"
    "- 增加一个「信任危机」的中间节点\n"
    "- 让两人在第5-6章才真正开始相互信赖\n\n"
    "你想先讨论哪个问题？或者你已经有了自己的思路？"
)

_PROACTIVE_WORLDBOOK = (
    "我注意到你的世界书中有一些可以补充的地方，想和你确认几个问题：\n\n"
    "**1. 星门的分布规律**\n"
    "目前设定中星门「均处于休眠状态」，但没有说明已知星门的数量和分布。"
    "这会影响后续的剧情展开——是只有一座星门（唯一性增加紧迫感），还是多座（可以形成网络）？\n\n"
    "**2. 远古文明与人类的关系**\n"
    "远古文明灭亡了，但他们是否和人类有某种关联？例如：\n"
    "- 人类是远古文明的后裔？\n"
    "- 远古文明的灭亡与人类的崛起有因果关系？\n"
    "- 完全无关的两个文明？\n\n"
    "**3. 联邦的政治格局**\n"
    "一个管辖200个恒星系的政府，内部是否存在派系？这些派系对「远古文明遗产」的态度是否不同？\n\n"
    "这些问题会影响整体世界观的一致性。要不要我帮你整理一个设定文档？"
)

_DEFAULT_RESPONSE = (
    "收到你的请求。这是测试模式下的模拟AI回复。\n\n"
    "在测试模式中，系统会返回预设的内容来验证各功能模块的交互流程。"
    "如需使用真实AI生成内容，请在「设置→模型供应商」中配置并启用一个可用的供应商。\n\n"
    "当前可测试的功能包括：\n"
    "- 头脑风暴与大纲生成\n"
    "- 风格校准\n"
    "- Creative Writing Pipeline\n"
    "- 章节评估\n"
    "- 角色AI助手\n"
    "- 世界书一致性检查\n"
    "- AI 主动提问与建议"
)


def _pick_response(messages: list[LLMMessage]) -> str:
    """Pick a context-appropriate response based on prompt content."""
    combined = " ".join(m.content for m in messages)
    combined_lower = combined.lower()

    # ── Scene Director (pipeline step 1): expects JSON scene plan ──
    if any(kw in combined for kw in ["分镜场景计划", "拆解为分镜", "场景计划", "scene plan"]):
        return _SCENE_PLAN_JSON

    # ── Evaluator: expects JSON evaluation ──
    if any(kw in combined for kw in ["综合评估", "质量评估", "评分", "evaluate", "evaluation"]):
        return _EVALUATION_JSON

    # ── Auto-outline: expects JSON outline ──
    if any(kw in combined for kw in ["自动生成大纲", "auto_outline", "自动大纲"]):
        return _AUTO_OUTLINE

    # ── A/B compare ──
    if any(kw in combined for kw in ["ab_compare", "对比分析", "版本对比"]):
        return _AB_COMPARE_RESULT

    # ── World book consistency check ──
    if any(kw in combined for kw in ["一致性检查", "世界观一致性", "内部矛盾", "逻辑冲突", "世界书条目"]):
        return _CONSISTENCY_CHECK

    # ── Character profile generation (JSON output) ──
    if any(kw in combined for kw in [
        "角色档案", "角色设计师。根据角色名字", "generate-profile",
    ]):
        return _CHARACTER_GENERATE_PROFILE_JSON

    # ── Character AI: full profile generation (when asked for JSON) ──
    if any(kw in combined for kw in ["生成完整人设", "丰富背景故事", "设计说话风格"]):
        return _CHARACTER_FULL_PROFILE

    # ── Character AI: conversational mode ──
    if any(kw in combined for kw in ["角色设计师", "角色设计", "角色助手", "增加性格矛盾"]):
        return _CHARACTER_CHAT

    # ── Brainstorm (expects JSON with answer + follow_up) ──
    if any(kw in combined for kw in ["创作顾问", "追问", "follow_up"]):
        return _BRAINSTORM_JSON

    # ── Calibration with feedback history (expects JSON with text + analysis) ──
    if any(kw in combined for kw in ["之前的评分和评论反馈", "调整风格参数", "调整风格方向"]):
        return _CALIBRATION_FEEDBACK_JSON

    # ── Outline brainstorming (multi-turn chat) ──
    if any(kw in combined for kw in ["策划编辑", "故事大纲", "构思", "brainstorm"]):
        # Check if it's a follow-up (has prior messages)
        user_msgs = [m for m in messages if m.role == "user"]
        if len(user_msgs) > 1:
            return _OUTLINE_FOLLOWUP
        return _OUTLINE_BRAINSTORM

    # ── AI proactive questioning ──
    if any(kw in combined for kw in ["主动提问", "proactive", "审阅", "建议", "帮我检查"]):
        if any(kw in combined for kw in ["世界书", "worldbook", "设定", "世界观"]):
            return _PROACTIVE_WORLDBOOK
        return _PROACTIVE_QUESTION

    # ── Chinese keyword fallbacks ──

    # Outline & brainstorm
    if any(kw in combined for kw in ["大纲", "outline", "情节", "故事线", "头脑风暴"]):
        return _OUTLINE_BRAINSTORM

    # Calibration / style sample
    if any(kw in combined for kw in ["校准", "样本", "calibrat", "风格要求"]):
        return _CALIBRATION_SAMPLE

    # Editor rewrite / assemble
    if any(kw in combined for kw in ["润色", "重写", "rewrite", "修改", "改写", "表演记录", "剪辑成", "文学风格化", "章节正文"]):
        return _EDITOR_REWRITE

    # Quick generate / creative writing
    if any(kw in combined for kw in [
        "章节内容", "写出", "续写", "章节大纲", "quick",
        "小说写作", "写作ai", "生动",
    ]):
        return _QUICK_GENERATE

    # Actor prose (pipeline)
    if any(kw in combined for kw in ["演绎", "actor", "对话", "prose", "正文", "角色扮演", "电影剧本", "表演", "第一人称视角"]):
        return _PIPELINE_ACTOR_PROSE

    # Character-related
    if any(kw in combined for kw in ["角色", "人设", "character", "性格"]):
        return _CHARACTER_CHAT

    # Style / writing
    if any(kw in combined for kw in ["风格", "style", "文笔"]):
        return _CALIBRATION_SAMPLE

    return _DEFAULT_RESPONSE


class MockProvider(BaseLLMProvider):
    """Deterministic mock provider for test mode."""

    def __init__(self, config: ProviderConfig | None = None):
        if config is None:
            config = ProviderConfig(
                provider_type="mock",
                model_name="mock-test-v1",
                max_tokens=4096,
                temperature=0.0,
            )
        super().__init__(config)

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        content = _pick_response(messages)
        # Simulate realistic token counts
        input_tokens = sum(len(m.content) // 2 for m in messages)
        output_tokens = len(content) // 2
        return LLMResponse(
            content=content,
            model="mock-test-v1",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason="stop",
            raw={"mock": True},
        )

    async def generate_stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        content = _pick_response(messages)
        # Stream in small chunks for realistic effect
        chunk_size = 15
        for i in range(0, len(content), chunk_size):
            yield content[i : i + chunk_size]

    async def health_check(self) -> bool:
        return True

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0
