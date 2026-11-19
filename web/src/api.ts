export type Channel = "wecom" | "dingtalk" | "feishu";

export const CHANNEL_LABEL: Record<Channel, string> = {
  wecom: "企业微信",
  dingtalk: "钉钉",
  feishu: "飞书",
};

export interface Message {
  id: string;
  channel: Channel;
  external_id: string;
  group_id: string;
  group_name: string | null;
  is_external_group: boolean;
  sender_id: string;
  sender_name: string | null;
  msg_type: string;
  content: string;
  sent_at: string;
  received_at: string;
}

export async function fetchMessages(channel?: Channel): Promise<Message[]> {
  const qs = channel ? `?channel=${channel}` : "";
  const res = await fetch(`/api/messages${qs}`);
  if (!res.ok) throw new Error(`加载消息失败：${res.status}`);
  return res.json();
}
