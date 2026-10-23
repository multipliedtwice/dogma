"""Render the existing site typography/palette as two PNG sharing cards.

Development-only build helper; needs Pillow and installed Georgia/Segoe UI fonts.
Font files are not copied into the site.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
FONTS=Path('C:/Windows/Fonts')


def font(name,size):return ImageFont.truetype(str(FONTS/name),size)


def build():
    for language,lines,subtitle in [
        ('en',['Books, brought','to voice.'],'Local reading. AI narration. A story to listen to.'),
        ('ru',['Книги обретают','голос.'],'Чтение и озвучка книг. Послушайте историю.')]:
        image=Image.new('RGB',(1200,630),'#161b18');draw=ImageDraw.Draw(image)
        draw.rounded_rectangle((45,45,1155,585),radius=18,outline='#46503e',width=2)
        draw.text((82,66),'dogma.',font=font('georgiab.ttf',48),fill='#f0ede3')
        draw.line((84,145,1118,145),fill='#46503e',width=2)
        draw.text((83,196),lines[0],font=font('georgia.ttf',68),fill='#f0ede3')
        draw.text((83,283),lines[1],font=font('georgia.ttf',78),fill='#b8c8ab')
        draw.text((86,420),subtitle,font=font('segoeui.ttf',25),fill='#b7c0ac')
        draw.text((86,522),'EN / РУ  ·  multipliedtwice.github.io/dogma/',font=font('segoeui.ttf',20),fill='#d3ae78')
        for index,height in enumerate([40,65,95,135,165,135,95,65,40]):
            x=955+index*15;draw.rounded_rectangle((x,285-height//2,x+5,285+height//2),radius=3,fill='#d3ae78')
        image.save(ROOT/f'website/assets/social-{language}.png',optimize=True)


if __name__=='__main__':build()
