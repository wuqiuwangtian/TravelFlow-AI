import Link from "next/link";
import { ArrowUpRight, ChevronDown, ExternalLink } from "lucide-react";

import { Card } from "@/components/ui";

export const metadata = { title: "使用指南 · TravelFlow AI" };

const CHAPTERS = [
  {
    id: "guide-start",
    title: "出发前，把想法变成行程",
    body: "还没确定去哪，可以先说季节、天数、预算和偏好；已有目的地，就直接说想去哪里、想怎么玩。",
    points: [
      "先在设置中配置模型与检索服务，并测试已保存的连接。修改后需要先保存再测试。",
      "在工作台新建旅行。常用偏好可主动带入，也可以这次单独说明。",
      "生成后，在同一次对话中继续调整。路线、预估与路书一起核对，可撤销的修改会给出撤销入口。",
    ],
    links: [
      { href: "/", label: "去工作台" },
      { href: "/settings", label: "配置服务" },
    ],
  },
  {
    id: "guide-ontrip",
    title: "旅途中，看下一站，也记下实际发生",
    body: "打开已有旅行，手机先看今日路线；导航、完成状态、费用和对话都围绕同一份行程。",
    points: [
      "地点导航默认以上一站为起点，也可切换当前位置。当前位置需要浏览器定位授权；未确认的地点仍需核对。",
      "在饮食、住宿、交通和门票等项目旁填写实付，也可添加自定义消费，查看与预估的差额。",
      "临时少走一点、换个景点，可以继续对话修改；已记消费独立保留。途中完成状态、预约与回款也可在相应条目处理。",
    ],
    links: [{ href: "/sessions", label: "打开已有旅行" }],
  },
  {
    id: "guide-after",
    title: "回来后，留下自己的旅行记录",
    body: "在旅行内写回顾、选择照片和标记结束；保存后，可以在旅行回顾与生涯中查看来源。",
    points: [
      "上传、选入和封面先进入草稿，保存回顾后才确认关联。照片可按完整比例打开，说明文字保留原文。",
      "AI 写作先生成候选，采用后填入编辑区，再保存。是否使用照片等素材，由你主动选择。",
      "生涯依据已记录的完成、到访与消费汇总。计划地点和照片标注不直接当作实际到访。",
    ],
    links: [
      { href: "/recaps", label: "查看旅行回顾" },
      { href: "/career", label: "查看旅行生涯" },
    ],
  },
];

const FAQ = [
  {
    id: "guide-branches",
    q: "分支、版本和高级工程有什么区别？",
    a: (
      <>
        <p>
          版本记录同一方案的历次变化；分支保留不同方向，例如亲子版和美食版。分支对比会读取各分支的最新路书，最多同时比较三份；“设为当前”只切换默认方案。
        </p>
        <p>
          高级工程从旅行内进入，用节点、端口和连线拆解生成过程。可以从模板开始，再自由改参数、增删节点和调整依赖；检查区显示参数与独立产物。工程保存、运行和正式采用是不同操作，运行完成后仍需核对采用结果。
        </p>
        <Link href="/compare" className="tf-action-link tf-page-link">
          比较旅行方案
          <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </>
    ),
  },
  {
    id: "guide-recovery",
    q: "点击后还在等待，或者保存失败，怎么办？",
    a: (
      <>
        <p>
          先看操作旁的等待或错误说明，避免重复提交。失败后保留的文字和选择可继续使用，按原入口重试；如果提示版本已变化，先核对当前方案，再决定下一步。
        </p>
        <p>
          模型鉴权失败时，到设置核对服务商、模型、接口地址和仍有效的
          Key，保存后再测试连接。密钥留空表示保留已存值；被服务商删除或撤销的密钥需要重新填写。
        </p>
        <Link href="/settings" className="tf-action-link tf-page-link">
          检查已保存配置
        </Link>
      </>
    ),
  },
  {
    id: "guide-map",
    q: "地图或图片加载失败，会影响整份行程吗？",
    a: (
      <>
        <p>
          可以继续阅读文字路线和地点信息。先核对地图服务配置与网络，再按失败处的重试入口操作；外部导航仍依赖已确认的地点和导航服务。
        </p>
        <p>
          景点小图只作辅助，缺图不需要补齐才能使用。旅行照片可重试加载，完整查看保留原比例，不修改原文件。
        </p>
      </>
    ),
  },
  {
    id: "guide-batch",
    q: "旅途中几件事可以一起告诉 AI 吗？",
    a: (
      <>
        <p>
          可以，例如“第1天第1站已完成，午餐花了86元，地铁花了5元，后面安排轻松一点”。用逗号或分号分开，后续短句会沿用最近明确的行程日期。
        </p>
        <p>
          状态、消费、已有预约、退款和报销按顺序处理，行程调整放在最后。若某项不明确或调整失败，本条消息的操作都不会保存；成功后一起保存为一个新版本。
        </p>
      </>
    ),
  },
  {
    id: "guide-sources",
    q: "如何核对票价、开放时间和预约信息？",
    a: (
      <>
        <p>
          查看路书中的来源、检索时间与办理渠道，出发前到官方页面核对票价、开放时间和预约要求。缺少来源或检索失败时，可以继续要求补充核实；生成内容不代表已经订票或预约成功。
        </p>
        <p>生成速度取决于检索、模型与工程结构，以当前进度为准。</p>
      </>
    ),
  },
  {
    id: "guide-services",
    q: "模型和检索服务如何选择？",
    a: (
      <>
        <p>
          在设置中查看当前支持的服务及说明，使用自己的有效配置。本地 Ollama
          需要自行安装并准备模型；是否联网还取决于所选检索、地图等功能。
        </p>
        <p>
          各服务的额度、计费与可用性以官方说明为准，可从以下页面查看；填写配置后先保存，再测试。
        </p>
        <div className="tf-help-external">
          {[
            ["https://duckduckgo.com/", "DuckDuckGo"],
            ["https://cloud.baidu.com/doc/qianfan/index.html", "百度千帆"],
            ["https://docs.tavily.com/", "Tavily"],
            ["https://open.bochaai.com/", "博查 Bocha"],
          ].map(([href, label]) => (
            <a
              key={href}
              href={href}
              className="tf-action-link tf-page-link"
              target="_blank"
              rel="noopener noreferrer"
            >
              {label}
              <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
              <span className="sr-only">（新窗口）</span>
            </a>
          ))}
        </div>
      </>
    ),
  },
  {
    id: "guide-privacy",
    q: "旅行数据和图片保存在哪里？",
    a: (
      <>
        <p>
          会话、路书与图片保存在运行 TravelFlow
          服务的机器上，配置写入该服务的环境文件。若服务运行在其他机器上，这里就不是当前浏览器所在设备。
        </p>
        <p>
          使用
          AI、检索或地图时，会向所选服务发送必要内容。凭证识别会发送去除元数据后的图片预览给当前模型；回顾写作中是否使用图片等素材由你选择。不要在旅行正文或截图中公开自己的密钥。
        </p>
      </>
    ),
  },
];

export default function HelpPage() {
  return (
    <div className="tf-page tf-help-page">
      <div className="tf-page-heading">
        <div>
          <h1>使用指南</h1>
          <p>从一句旅行想法，到旅途中调整，再到回来后的记录。</p>
        </div>
        <Link href="/" className="tf-action-link tf-page-link">
          开始一次旅行
          <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
        </Link>
      </div>
      <div className="tf-help-layout">
        <nav className="tf-help-nav" aria-label="指南章节">
          {[
            ...CHAPTERS.map(({ id, title }) => ({ id, title })),
            { id: "guide-questions", title: "常见问题与恢复" },
          ].map((item) => (
            <a
              key={item.id}
              className="tf-action-link tf-page-link"
              href={`#${item.id}`}
            >
              {item.title}
            </a>
          ))}
        </nav>
        <div className="min-w-0">
          {CHAPTERS.map((chapter, index) => (
            <section
              key={chapter.id}
              className="tf-help-chapter"
              aria-labelledby={chapter.id}
            >
              <div className="tf-help-chapter-heading">
                <span aria-hidden="true">0{index + 1}</span>
                <h2 id={chapter.id} tabIndex={-1}>
                  {chapter.title}
                </h2>
              </div>
              <p>{chapter.body}</p>
              <ol>
                {chapter.points.map((point) => (
                  <li key={point}>{point}</li>
                ))}
              </ol>
              <div className="tf-help-actions">
                {chapter.links.map((link) => (
                  <Link
                    key={link.href}
                    className="tf-action-link tf-page-link"
                    href={link.href}
                  >
                    {link.label}
                    <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5" />
                  </Link>
                ))}
              </div>
            </section>
          ))}
          <section aria-labelledby="guide-questions">
            <h2
              id="guide-questions"
              className="tf-section-title tf-help-questions"
              tabIndex={-1}
            >
              常见问题与恢复
            </h2>
            <Card className="tf-help-faq">
              {FAQ.map((item) => (
                <details key={item.id} id={item.id}>
                  <summary>
                    <span>{item.q}</span>
                    <ChevronDown
                      aria-hidden="true"
                      className="tf-help-chevron h-4 w-4"
                    />
                  </summary>
                  <div className="tf-help-answer">{item.a}</div>
                </details>
              ))}
            </Card>
          </section>
        </div>
      </div>
    </div>
  );
}
