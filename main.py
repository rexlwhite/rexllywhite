import os
import time
import asyncio
import sqlite3
from urllib.parse import quote
import httpx
import discord
from discord.ext import commands
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

# ==========================================
# 1. โหลดค่าคอนฟิกจาก Environment Variables
# ==========================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
GUILD_ID = int(os.getenv("GUILD_ID")) if os.getenv("GUILD_ID") else 0
ROLE_ID = int(os.getenv("ROLE_ID")) if os.getenv("ROLE_ID") else 0
PORT = int(os.getenv("PORT", 10000))

# ==========================================
# 2. ตั้งค่าและเตรียมฐานข้อมูล SQLite
# ==========================================
DB_FILE = "database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_tokens (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER
        )
    """)
    conn.commit()
    conn.close()

# เรียกใช้งานฟังก์ชันสร้างตารางเมื่อเปิดโปรแกรม
init_db()

# ตั้งค่า Discord Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        encoded_redirect = quote(REDIRECT_URI, safe='')
        oauth_url = f"https://discord.com{CLIENT_ID}&redirect_uri={encoded_redirect}&response_type=code&scope=identify%20guilds.join"
        self.add_item(discord.ui.Button(label="ลิงก์บัญชี และ รับยศ", url=oauth_url, style=discord.ButtonStyle.link))

@bot.event
async def on_ready():
    print(f"Bot ออนไลน์แล้วในชื่อ: {bot.user}")

# ------------------------------------------
# คำสั่งแอดมิน 1: สร้างปุ่มรับยศในเซิร์ฟเวอร์หลัก
# ------------------------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    embed = discord.Embed(
        title="ระบบรับยศอัตโนมัติ",
        description="กรุณาคลิกปุ่มด้านล่างเพื่อเชื่อมต่อบัญชี Discord ของคุณและรับยศ",
        color=discord.Color.from_rgb(88, 101, 242)
    )
    await ctx.send(embed=embed, view=VerifyView())

# ------------------------------------------
# คำสั่งแอดมิน 2: ดึงคนเข้าเซิร์ฟเวอร์ใหม่
# ------------------------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def backup(ctx):
    target_guild_id = ctx.guild.id
    status_message = await ctx.send(f"เริ่มดึงสมาชิกจากฐานข้อมูลเข้าสู่เซิร์ฟเวอร์นี้ ID: {target_guild_id}...")
    
    success_count = 0
    fail_count = 0
    already_in_server = 0

    # ดึงข้อมูลผู้ใช้ทั้งหมดจาก SQLite
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, access_token FROM user_tokens")
    users = cursor.fetchall()
    conn.close()

    for user in users:
        user_id = int(user[0])
        access_token = user[1]
        
        member = ctx.guild.get_member(user_id)
        if member:
            already_in_server += 1
            continue

        url = f"https://discord.com{target_guild_id}/members/{user_id}"
        headers = {
            "Authorization": f"Bot {DISCORD_TOKEN}",
            "Content-Type": "application/json"
        }
        data = {"access_token": access_token}

        async with httpx.AsyncClient() as client:
            resp = await client.put(url, json=data, headers=headers)
            # แก้ไขเงื่อนไขเช็ค Status Code 201 (เข้าใหม่) และ 204 (อยู่ในห้องอยู่แล้ว)
            if resp.status_code in:
                success_count += 1
                print(f"ดึงผู้ใช้ {user_id} เข้าสำเร็จ")
            else:
                fail_count += 1
                print(f"ดึงผู้ใช้ {user_id} ล้มเหลว: {resp.text}")
        
        await asyncio.sleep(1)

    await status_message.edit(
        content=f"ทำการดึงข้อมูลเสร็จสิ้น\n"
                f"ดึงเข้าสำเร็จ: {success_count} คน\n"
                f"อยู่ในเซิร์ฟเวอร์แล้ว: {already_in_server} คน\n"
                f"ล้มเหลว Token หมดอายุ หรือ ถอนสิทธิ์: {fail_count} คน"
    )

# ==========================================
# 3. ระบบ Web Server (FastAPI) รับ Callback
# ==========================================
app = FastAPI()

@app.get("/")
def home():
    return {"status": "Bot Backup System is online!"}

@app.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(request: Request):
    code = request.query_params.get('code')
    if not code:
        return "ไม่พบ Code สำหรับการยืนยันตัวตน", 400

    async with httpx.AsyncClient() as client:
        # แลกเปลี่ยน Code เป็น Access Token
        token_data = {
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': REDIRECT_URI
        }
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        resp = await client.post('https://discord.com', data=token_data, headers=headers)
        if resp.status_code != 200:
            return "เกิดข้อผิดพลาดในการแลกเปลี่ยน Token", 500
        token_res = resp.json()
            
        access_token = token_res.get('access_token')
        refresh_token = token_res.get('refresh_token')
        expires_in = token_res.get('expires_in')

        # ดึงข้อมูลโปรไฟล์ผู้ใช้
        user_headers = {'Authorization': f'Bearer {access_token}'}
        resp = await client.get('https://discord.com', headers=user_headers)
        if resp.status_code != 200:
            return "ไม่สามารถดึงข้อมูลผู้ใช้ได้", 500
        user_res = resp.json()

        user_id = str(user_res['id'])
        username = user_res['username']

        # บันทึกหรืออัปเดตข้อมูลลง SQLite
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_tokens (user_id, username, access_token, refresh_token, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                expires_at=excluded.expires_at
        """, (user_id, username, access_token, refresh_token, int(time.time() + expires_in)))
        conn.commit()
        conn.close()
        
        print(f"บันทึกกุญแจถาวรของคุณ {username} ({user_id}) เรียบร้อย")

        guild = bot.get_guild(GUILD_ID)
        if not guild:
            try: guild = await bot.fetch_guild(GUILD_ID)
            except: return "ไม่พบเซิร์ฟเวอร์ดิสคอร์ดหลัก", 500

        try:
            member = await guild.fetch_member(int(user_id))
        except discord.NotFound:
            return "<h1>เกิดข้อผิดพลาด</h1><p>ไม่พบคุณในเซิร์ฟเวอร์ กรุณาเข้าเซิร์ฟเวอร์ก่อนกดรับยศ</p>", 404

        role = guild.get_role(ROLE_ID)
        if role and member:
            try:
                await member.add_roles(role)
                return f"<h1>ยืนยันตัวตนสำเร็จ</h1><p>บันทึกข้อมูลและมอบยศให้คุณ {username} เรียบร้อยแล้ว</p>"
            except discord.Forbidden:
                return "บอทไม่มีสิทธิ์ให้ยศนี้ กรุณาตรวจสอบลำดับยศของบอท", 500
        else:
            return "ไม่พบยศที่กำหนดไว้ในเซิร์ฟเวอร์", 500

# ==========================================
# 4. สั่งรันบอทและเซิร์ฟเวอร์พร้อมกัน
# ==========================================
async def start_fastapi():
    import uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    bot.loop.create_task(start_fastapi())
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
