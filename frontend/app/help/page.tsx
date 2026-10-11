import { CircleHelp, ExternalLink, KeyRound, Play, Sparkles } from "lucide-react";

import { Card } from "@/components/ui";

export const metadata = { title: "使用指南 · TravelFlow AI" };

const STEPS = [
  {
    icon: Play,
    title: "1 · 启动服务",
    body: "双击 start 脚本，等浏览器自动打开即可。不需要懂命令行。",
  },
  {
    icon: KeyRound,
    title: "2 · 配置模型",
    body: "在「设置」里选一个模型服务商并填入 API Key；不想折腾可以选本地 Ollama。",
  },
  {
    icon: Sparkles,
    title: "3 · 开始规划",
    body: "回到工作台点「新建旅行」，说出目的地就行，剩下的交给我。",
  },
];

const FAQ = [
  {
    q: "API Key 是什么？去哪里申请？",
    a: (
      <>
        Key 相当于模型服务的「门票」。推荐 DeepSeek（性价比高，官网可申请）。
        如果你完全不想注册任何服务，可以在设置里选「本地 Ollama」，
        模型跑在自己电脑上，完全免费、也不联网。
      </>
    ),
  },
  {
    q: "检索服务要花钱吗？",
    a: (
      <>
        各家服务的<b>免费额度与计费方式由其官方制定，可能随时调整</b>
        ，本项目不做承诺，请以官方页面为准：
        <span className="mt-2 flex flex-wrap gap-2">
          <a
            className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-700 no-underline hover:bg-brand-100"
            href="https://duckduckgo.com/"
            target="_blank"
            rel="noopener noreferrer"
          >
            DuckDuckGo（无需 Key）<ExternalLink className="h-3 w-3" />
          </a>
          <a
            className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-700 no-underline hover:bg-brand-100"
            href="https://cloud.baidu.com/doc/qianfan/index.html"
            target="_blank"
            rel="noopener noreferrer"
          >
            百度千帆<ExternalLink className="h-3 w-3" />
          </a>
          <a
            className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-700 no-underline hover:bg-brand-100"
            href="https://docs.tavily.com/"
            target="_blank"
            rel="noopener noreferrer"
          >
            Tavily<ExternalLink className="h-3 w-3" />
          </a>
          <a
            className="inline-flex items-center gap-1 rounded-md border border-brand-200 bg-brand-50 px-2.5 py-1 text-xs font-semibold text-brand-700 no-underline hover:bg-brand-100"
            href="https://open.bochaai.com/"
            target="_blank"
            rel="noopener noreferrer"
          >
            博查 Bocha<ExternalLink className="h-3 w-3" />
          </a>
        </span>
        <span className="mt-2 block">
          <b>提示</b>：即使某家的额度政策变化，本项目也不会因此用不了 ——
          你随时可以在设置里一键切换到其他检索源。
        </span>
      </>
    ),
  },
  {
    q: "路书里的信息可靠吗？",
    a: (
      <>
        所有事实性信息（票价、开放时间）都标注了来源编号，可以点开核对。
        引用链接由系统直接从检索结果获取，<b>不经过大模型改写</b>。
        <br />
        涉及购票和预约的事项，路书会单独列出<b>官方办理渠道</b>，建议你出发前自己去核实一次。
        每份路书也会标注「信息检索时间」，因为价格和政策随时可能变。
      </>
    ),
  },
  {
    q: "为什么要等十几秒才能生成？",
    a: (
      <>
        系统需要实时联网检索交通、景点和避雷信息（三路并行），
        再让模型基于这些<b>真实资料</b>生成内容。这是为了信息真实，而不是让模型凭空编造。
      </>
    ),
  },
  {
    q: "「分支」是干什么用的？",
    a: (
      <>
        如果你拿不定主意，可以从同一个起点生成几个不同方案（例如「亲子版」和「美食版」）并排对比。
        <br />
        改来改去发现还是最初那版好？在版本历史里点最早的版本「回到这里」就行，原来的版本不会丢。
      </>
    ),
  },
  {
    q: "数据会上传到云端吗？",
    a: (
      <>
        不会。所有会话、路书、配置都保存在你电脑上的一个数据库文件里。
        只有调用大模型和检索服务时，必要的文字才会发送出去。
      </>
    ),
  },
];

export default function HelpPage() {
  return (
    <div className="mx-auto max-w-3xl px-5 py-8">
      <div className="mb-6 flex items-center gap-2.5">
        <CircleHelp className="h-5 w-5 text-brand-500" />
        <div>
          <h1 className="text-lg font-bold">使用指南</h1>
          <p className="text-xs text-slate-400">三步开始，不需要任何技术背景</p>
        </div>
      </div>

      <div className="mb-7 grid gap-3.5 sm:grid-cols-3">
        {STEPS.map((step) => {
          const Icon = step.icon;
          return (
            <Card key={step.title} className="p-4">
              <div className="mb-2.5 flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-teal-500 text-white">
                <Icon className="h-4 w-4" />
              </div>
              <h3 className="text-sm font-bold text-slate-800">{step.title}</h3>
              <p className="mt-1 text-xs leading-relaxed text-slate-500">{step.body}</p>
            </Card>
          );
        })}
      </div>

      <Card className="divide-y divide-slate-100">
        {FAQ.map((item) => (
          <details key={item.q} className="group px-5 py-3.5">
            <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-semibold text-slate-700">
              {item.q}
              <span className="text-xs text-slate-300 group-open:hidden">▾</span>
              <span className="hidden text-xs text-slate-300 group-open:inline">▴</span>
            </summary>
            <div className="mt-2.5 text-xs leading-relaxed text-slate-500">{item.a}</div>
          </details>
        ))}
      </Card>
    </div>
  );
}
