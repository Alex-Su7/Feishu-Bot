import requests
import json
import openai
from lark_oapi import EventDispatcherHandler, ws, JSON, im, LogLevel

# 代理配置
proxies = {
    '填入代理地址',
    '填入代理地址'
}

class FeishuConfig:
    '''飞书API的配置信息'''
    APP_ID = '输入飞书APP_id'
    APP_SECRET = '输入飞书APP_id'
    # 增加代理设置
    PROXIES = proxies

class FeishuApi:
    '''FeishuApi类用于处理与飞书API的交互'''
    TOKEN_URL = 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal'
    REPLY_MESSAGE_URL_TEMPLATE = 'https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply'
    HEADERS = {'Content-Type': 'application/json; charset=utf-8'}
    OPENAI_API_KEY = '输入OpenAI Key'

    def __init__(self):
        self.session = requests.Session()
        # 应用代理设置
        self.session.proxies.update(FeishuConfig.PROXIES)
        self.token = self.get_token()
        openai.api_key = self.OPENAI_API_KEY

    def get_token(self):
        '''获取飞书API的访问令牌'''
        data = {'app_id': FeishuConfig.APP_ID, 'app_secret': FeishuConfig.APP_SECRET}
        response = self.session.post(self.TOKEN_URL, headers=self.HEADERS, json=data)
        response.raise_for_status()
        return response.json().get('tenant_access_token')


    def reply_message(self, message_id, user_id, message):
        '''回复飞书群聊消息'''
        url = self.REPLY_MESSAGE_URL_TEMPLATE.format(message_id=message_id)
        content = json.dumps({
            "text": f"<at user_id=\"{user_id}\"></at> {message}"
        })
        data = {
            "content": content,
            "msg_type": "text"
        }
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json; charset=utf-8'
        }
        print(f"Sending message to {url} with data: {data} and headers: {headers}")
        response = self.session.post(url, headers=headers, json=data)
        try:
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            print(f"Failed to send message: {response.text}")
            raise e

    def generate_reply_with_chatgpt(self, message):
        '''使用ChatGPT模型生成回复'''
        try:
            response = openai.ChatCompletion.create(
                model="gpt-4-turbo",
                messages=[{"role": "system", "content": "You are a helpful assistant."},
                          {"role": "user", "content": message}]
            )
            return response['choices'][0]['message']['content'].strip()
        except Exception as e:
            print(f"Error during OpenAI API call: {e}")
            return "抱歉，我无法现在提供回答。"

# 模拟数据库表，用于存储对话历史和已处理的消息ID
MsgTable = {}
ProcessedMessages = set()

def save_conversation(session_id, question, answer):
    MsgTable[session_id] = MsgTable.get(session_id, []) + [(question, answer)]

def get_conversation(session_id):
    return MsgTable.get(session_id, [])

def clear_conversation(session_id):
    if session_id in MsgTable:
        del MsgTable[session_id]

def build_prompt(session_id, new_question):
    conversation_history = get_conversation(session_id)
    prompt = ""
    for question, answer in conversation_history:
        prompt += f"User: {question}\nAssistant: {answer}\n"
    prompt += f"User: {new_question}\nAssistant:"
    return prompt

def handle_p2_im_message(data: im.v1.P2ImMessageReceiveV1):
    data_dict = json.loads(JSON.marshal(data))
    message_id = data_dict["event"]["message"]["message_id"]
    
    # 防止对同一个message_id的重复处理
    if message_id in ProcessedMessages:
        print(f"Message {message_id} already handled, skipping.")
        return
    else:
        ProcessedMessages.add(message_id)

    print(f"Handling message event: {data.header.event_id}")
    
    # 检查消息是否提及了机器人，只有在提及的情况下才回复
    event = data_dict.get('event', {})
    message = event.get('message', {})
    mentions = message.get('mentions', [])
    is_bot_mentioned = any(mention.get('key') == '@_user_1' for mention in mentions)
    
    if not is_bot_mentioned and message.get('chat_type') == 'group':
        # 如果不是群聊中提及机器人的消息，则不处理
        print(f"Bot was not mentioned in group chat message {message_id}, skipping.")
        return
    
    content = eval(message.get('content', '{}')).get("text", "")
    content = content.replace('"}', '').strip()
    
    # 如果机器人被提及，或者在私聊中，才继续处理消息
    user_id = data_dict["event"]["sender"]["sender_id"]["user_id"]
    session_id = f"{user_id}_{message_id}"  # 简化的会话ID

    feishu = FeishuApi()
    prompt = build_prompt(session_id, content)
    chatgpt_reply = feishu.generate_reply_with_chatgpt(prompt)
    feishu.reply_message(message_id=message_id, user_id=user_id, message=f'ChatGPT回复:{chatgpt_reply}')
    save_conversation(session_id, content, chatgpt_reply)

def main():
    '''启动飞书长连接 WebSocket客户端'''
    event_handler = EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(handle_p2_im_message) \
        .build()

    cli = ws.Client(FeishuConfig.APP_ID, FeishuConfig.APP_SECRET, event_handler=event_handler, log_level=LogLevel.DEBUG)
    cli.start()

if __name__ == "__main__":
    main()
