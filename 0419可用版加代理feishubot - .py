import json
import requests
import openai
from lark_oapi import EventDispatcherHandler, ws, JSON, im, LogLevel

ProcessedMessages = set()
# 读取配置文件
with open("config.json", "r") as file:
    config = json.load(file)

openai.api_key = config['OPENAI_API_KEY']
APP_ID = config['APP_ID']
APP_SECRET = config['APP_SECRET']
PROXIES = config['PROXIES']

class FeishuConfig:
    '''飞书API的配置信息'''
    APP_ID = config['APP_ID']
    APP_SECRET = config['APP_SECRET']
    PROXIES = config['PROXIES']

class FeishuApi:
    '''FeishuApi类用于处理与飞书API的交互'''
    TOKEN_URL = 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal'
    HEADERS = {'Content-Type': 'application/json; charset=utf-8'}
    OPENAI_API_KEY = config['OPENAI_API_KEY']

    def __init__(self):
        self.session = requests.Session()
        self.session.proxies.update(FeishuConfig.PROXIES)
        self.token = self.get_token()
        openai.api_key = self.OPENAI_API_KEY

    def get_token(self):
        '''获取飞书API的访问令牌'''
        data = {'app_id': FeishuConfig.APP_ID, 'app_secret': FeishuConfig.APP_SECRET}
        response = self.session.post(self.TOKEN_URL, headers=self.HEADERS, json=data)
        response.raise_for_status()
        return response.json().get('tenant_access_token')
    
    REPLY_MESSAGE_URL_TEMPLATE = 'https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply'


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
            print(f"Failed to send message: {e}")
            return None

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

# 调用DALL·E 3生成图片的函数
def generate_image(description):
    response = openai.Image.create(
        model="dall-e-3",
        prompt=description,
        n=1,
        size="1024x1024"
    )
    # 假设图片数据以base64编码返回
    return response['data'][0]['url']

# 发送图片到飞书的函数
def send_image_to_feishu(image_url, chat_id):
    feishu_api = FeishuApi()
    token = feishu_api.get_token()
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    data = {
        "chat_id": chat_id,
        "msg_type": "image",
        "content": {
            "image_key": image_url
        }
    }
    response = requests.post('https://open.feishu.cn/open-apis/im/v1/messages', headers=headers, json=data)
    return response.json()

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
    
    if message_id in ProcessedMessages:
        print(f"Message {message_id} already handled, skipping.")
        return

    print(f"Handling message event: {data.header.event_id}")
    
    event = data_dict.get('event', {})
    message = event.get('message', {})
    content = eval(message.get('content', '{}')).get("text", "")
    content = content.replace('"}', '').strip()
    chat_id = event.get('message', {}).get('chat_id', '')

    if content.startswith('/p '):
        description = content[3:]  # 获取描述文字
        try:
            image_url = generate_image(description)  # 尝试生成图片
            send_image_to_feishu(image_url, chat_id)  # 尝试发送图片到飞书
            ProcessedMessages.add(message_id)  # 只有成功发送后才标记为已处理
            print(f"Image sent successfully to {chat_id}")
        except Exception as e:
            print(f"Failed to process image command: {e}")
        return  # 处理完图片指令后返回

    # 检查消息是否提及了机器人，只有在提及的情况下才回复
    mentions = message.get('mentions', [])
    is_bot_mentioned = any(mention.get('key') == '@_user_1' for mention in mentions)
    if not is_bot_mentioned and message.get('chat_type') == 'group':
        # 如果不是群聊中提及机器人的消息，则不处理
        print(f"Bot was not mentioned in group chat message {message_id}, skipping.")
        return
    
    user_id = data_dict["event"]["sender"]["sender_id"]["user_id"]
    session_id = f"{user_id}_{message_id}"  # 简化的会话ID

    feishu = FeishuApi()
    prompt = build_prompt(session_id, content)
    try:
        chatgpt_reply = feishu.generate_reply_with_chatgpt(prompt)
        if chatgpt_reply:
            feishu.reply_message(message_id=message_id, user_id=user_id, message=f'ChatGPT回复:{chatgpt_reply}')
        else:
            feishu.reply_message(message_id=message_id, user_id=user_id, message='无法生成回复，请稍后再试。')
    except Exception as e:
        print(f"Failed to generate or send reply: {e}")
    save_conversation(session_id, content, chatgpt_reply if chatgpt_reply else "Failed to generate reply.")


def main():
    '''启动飞书长连接 WebSocket客户端'''
    event_handler = EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(handle_p2_im_message) \
        .build()

    cli = ws.Client(FeishuConfig.APP_ID, FeishuConfig.APP_SECRET, event_handler=event_handler, log_level=LogLevel.DEBUG)
    cli.start()

if __name__ == "__main__":
    main()
