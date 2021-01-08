import asyncio
import json
from pathlib import Path
import uuid

import tornado.ioloop
import tornado.web
import tornado.websocket
import aio_pika

tornado.ioloop.IOLoop.configure("tornado.platform.asyncio.AsyncIOLoop")
io_loop = tornado.ioloop.IOLoop.current()
asyncio.set_event_loop(io_loop.asyncio_loop)

BASE_DIR = Path(__file__).parent
AMQP_EXCHANGE_NAME = 'tornado_chat'


class MainHandler(tornado.web.RequestHandler):
    def get(self):
        room_id = self.get_argument('room_id')
        user_id = self.get_argument('user_id')
        self.render(
            'index.html',
            room_id=room_id,
            user_id=user_id,
        )


class ChatHandler(tornado.websocket.WebSocketHandler):
    async def open(self, room_id=None):
        self.room_id = room_id
        self.user_id = self.get_argument('user_id', None)
        if not self.user_id:
            self.write_message('user_id not provided')
            return self.close()
        amqp_connection = self.application.settings['amqp_connection']
        self.channel = await amqp_connection.channel()
        self.exchange = await self.channel.declare_exchange(
            name=AMQP_EXCHANGE_NAME, type=aio_pika.ExchangeType.HEADERS)
        self.queue = await self.channel.declare_queue(
            uuid.uuid4().hex,
            exclusive=True,
            auto_delete=True,
        )
        arguments = {'room_id': self.room_id, 'x-match': 'any'}
        await self.queue.bind(self.exchange, arguments=arguments)
        await self.queue.consume(self.amqp_message_callback)
        await self.write_message('ready')

    async def amqp_message_callback(self, message):
        async with message.process():
            decoded_body = message.body.decode()
            await self.write_message(decoded_body)

    async def on_message(self, message):
        message = f'{self.user_id} says: {message}'
        message = json.dumps({'from': self.user_id, 'message': message})
        message = message.encode()
        headers = {'room_id': self.room_id}
        pika_message = aio_pika.Message(
            message,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=headers,
        )
        await self.exchange.publish(pika_message, routing_key='')


urlpatterns = [
    (r'/', MainHandler),
    (r'/ws-chat/(?P<room_id>\w+)', ChatHandler),
]


async def make_app():
    amqp_connection = await aio_pika.connect_robust('amqp://localhost:5672')
    return tornado.web.Application(
        urlpatterns,
        template_path=BASE_DIR.joinpath('templates'),
        amqp_connection=amqp_connection,
    )


if __name__ == "__main__":
    io_loop = tornado.ioloop.IOLoop.current()
    app = io_loop.run_sync(make_app)
    app.listen(8000)
    io_loop.start()
