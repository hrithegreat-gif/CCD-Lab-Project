import io,struct,zlib
import pytest
from PIL import Image
from app.config import Settings
from app.imaging import decode_image,ImageRejected
from tests.conftest import image_bytes

def rejected(data,ctype,status,settings=None):
    with pytest.raises(ImageRejected) as e:decode_image(data,ctype,settings or Settings(_env_file=None))
    assert e.value.status_code==status

@pytest.mark.parametrize('fmt,ctype',[('JPEG','image/jpeg'),('PNG','image/png'),('WEBP','image/webp')])
def test_allowed_formats(fmt,ctype):
    with decode_image(image_bytes(fmt),ctype+'; ignored=parameter',Settings(_env_file=None)) as img:
        assert img.size==(64,48) and img.mode=='RGB' and not img.info

@pytest.mark.parametrize('mode',['RGBA','P','L'])
def test_image_modes(mode):
    with decode_image(image_bytes('PNG',mode),'image/png',Settings(_env_file=None)) as img:assert img.mode=='RGB'

def test_truncated_jpeg():rejected(image_bytes()[:-40],'image/jpeg',422)
def test_magic_mismatch():rejected(image_bytes(),'image/png',415)
def test_gif_disallowed():rejected(image_bytes('GIF'),'image/gif',415)
def test_empty_body():rejected(b'','image/jpeg',422)
def test_compressed_byte_cap():rejected(image_bytes(),'image/jpeg',413,Settings(_env_file=None,max_image_bytes=1024)) if len(image_bytes())>1024 else rejected(image_bytes()+b'x'*1024,'image/jpeg',413,Settings(_env_file=None,max_image_bytes=1024))

def test_huge_header_rejected_before_load(monkeypatch):
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',40000,40000,8,2,0,0,0))+chunk(b'IEND',b'')
    monkeypatch.setattr(Image.Image,'load',lambda *a:pytest.fail('pixel decode forbidden'))
    rejected(png,'image/png',422)

def test_pixel_limit_precedes_decode(monkeypatch):
    data=image_bytes('PNG',size=(1200,1000))
    from PIL import PngImagePlugin
    monkeypatch.setattr(PngImagePlugin.PngImageFile,'load',lambda *a:pytest.fail('pixel decode forbidden'))
    rejected(data,'image/png',422,Settings(_env_file=None,max_image_pixels=1000000))

def test_animated_webp_rejected():
    output=io.BytesIO()
    a=Image.new('RGB',(16,16),'red');b=Image.new('RGB',(16,16),'blue')
    a.save(output,format='WEBP',save_all=True,append_images=[b],duration=100,loop=0)
    rejected(output.getvalue(),'image/webp',422)

def test_exif_orientation_applied_and_metadata_removed():
    output=io.BytesIO();exif=Image.Exif();exif[274]=6;exif[270]='PRIVATE METADATA'
    Image.new('RGB',(80,40)).save(output,format='JPEG',exif=exif)
    with decode_image(output.getvalue(),'image/jpeg',Settings(_env_file=None)) as img:
        assert img.size==(40,80) and not img.info and not img.getexif()
