// Quantization quality eval: fp32 vs INT8(q8) on bge-small-zh-v1.5.
// Metric: Recall@5 on a small Chinese corpus with topic-grouped ground truth,
// plus per-pair cosine deviation between the two precisions.
import { pipeline, env } from '@huggingface/transformers';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
env.allowLocalModels = true;
env.localModelPath = path.resolve(__dirname, '../..', 'backend', 'public', 'models');

// 8 topics x 5 docs
const CORPUS = {
  weather: [
    '今天天气很好，适合去公园散步',
    '明天会有中雨，出门记得带伞',
    '这周气温持续升高，夏天要来了',
    '台风即将登陆，沿海地区注意防范',
    '冬天来了，北方开始下雪了',
  ],
  database: [
    '数据库向量检索技术发展迅速',
    'HNSW 索引支持近似最近邻查询',
    '全文检索使用 IK 分词器处理中文',
    'MySQL 是最流行的关系型数据库之一',
    '事务的 ACID 特性保证数据一致性',
  ],
  cooking: [
    '红烧肉的做法是先炒糖色再慢炖',
    '如何做出一杯好喝的手冲咖啡',
    '西红柿炒鸡蛋是最简单的家常菜',
    '煲汤要用小火慢慢熬才有味道',
    '做蛋糕需要把蛋白打发到硬性发泡',
  ],
  traffic: [
    '北京地铁高峰期非常拥挤',
    '共享单车解决了最后一公里问题',
    '高速公路节假日免费通行',
    '新能源车充电桩越来越普及',
    '高铁让城市之间的距离变短了',
  ],
  ai: [
    'Transformer 模型是注意力机制的核心',
    '大语言模型通过预训练学习语言规律',
    '强化学习让智能体在环境中试错',
    '卷积神经网络擅长处理图像数据',
    '知识蒸馏可以把大模型压缩成小模型',
  ],
  sports: [
    '昨晚的篮球比赛打到了加时赛',
    '马拉松比赛需要提前三个月训练',
    '乒乓球是中国的国球',
    '世界杯四年举办一次',
    '游泳是最全面的全身运动',
  ],
  finance: [
    '股票市场今天大幅上涨',
    '央行宣布下调存款准备金率',
    '基金定投适合长期投资者',
    '通货膨胀会影响货币购买力',
    '债券市场利率持续走低',
  ],
  health: [
    '每天喝八杯水有益身体健康',
    '早睡早起是保持精力的好习惯',
    '有氧运动可以增强心肺功能',
    '维生素 C 有助于提高免疫力',
    '久坐办公要注意保护颈椎',
  ],
};

// One query per topic, phrased differently from the docs (semantic match required).
const QUERIES = {
  weather: '最近适合外出活动吗',
  database: '怎么快速查找相似数据',
  cooking: '周末想给家人做顿饭',
  traffic: '通勤怎么走比较方便',
  ai: '神经网络是怎么工作的',
  sports: '最近有什么精彩的比赛',
  finance: '现在投资什么比较合适',
  health: '如何保持身体健康',
};

const topics = Object.keys(CORPUS);
const docs = [];
const docTopic = [];
for (const t of topics) {
  for (const d of CORPUS[t]) {
    docs.push(d);
    docTopic.push(t);
  }
}

function cos(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s; // vectors are L2-normalized
}

async function embedAll(p, texts) {
  const out = [];
  for (let i = 0; i < texts.length; i += 16) {
    const batch = texts.slice(i, i + 16);
    const res = await p(batch, { pooling: 'cls', normalize: true });
    const dim = res.dims[res.dims.length - 1];
    const flat = Array.from(res.data);
    for (let r = 0; r < batch.length; r++) out.push(flat.slice(r * dim, (r + 1) * dim));
  }
  return out;
}

function recallAtK(queryEmb, docEmbs, k) {
  const scores = docEmbs.map((d, i) => ({ i, s: cos(queryEmb, d) }));
  scores.sort((a, b) => b.s - a.s);
  return scores.slice(0, k).map((x) => x.i);
}

for (const dtype of ['q8', 'fp32']) {
  const p = await pipeline('feature-extraction', 'Xenova/bge-small-zh-v1.5', { dtype, device: 'cpu' });
  const docEmbs = await embedAll(p, docs);
  let totalRelevant = 0;
  let totalHit = 0;
  for (const t of topics) {
    const qEmb = (await embedAll(p, [QUERIES[t]]))[0];
    const top = recallAtK(qEmb, docEmbs, 5);
    const relevant = new Set(
      docTopic.map((x, i) => (x === t ? i : -1)).filter((i) => i >= 0),
    );
    const hit = top.filter((i) => relevant.has(i)).length;
    totalRelevant += relevant.size;
    totalHit += hit;
    console.log(`[${dtype}] query[${t}] "${QUERIES[t]}" hit@5=${hit}/5 -> ${top.map((i) => docTopic[i] + ':' + docs[i].slice(0, 10)).join(' | ')}`);
  }
  console.log(`\n[${dtype}] overall Recall@5 = ${(totalHit / totalRelevant * 100).toFixed(1)}% (${totalHit}/${totalRelevant})\n`);
  globalThis[`embs_${dtype}`] = docEmbs;
}

// Per-pair deviation between precisions
const a = globalThis.embs_q8;
const b = globalThis.embs_fp32;
let maxDev = 0;
let sumDev = 0;
let n = 0;
for (let i = 0; i < a.length; i++) {
  const c = cos(a[i], b[i]);
  const dev = Math.acos(Math.min(1, Math.max(-1, c))) * 180 / Math.PI;
  sumDev += dev;
  if (dev > maxDev) maxDev = dev;
  n++;
}
console.log(`q8 vs fp32 平均角度偏差: ${(sumDev / n).toFixed(3)}°  最大: ${maxDev.toFixed(3)}°`);
