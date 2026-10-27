/* Private DT2 1.17 panel overlay. Draw parameter schematics, never audio meters.
 * Original widget owns captions, selection and lock outlines. No saved state.
 */
typedef unsigned int u32;
typedef struct { u32 vt,w,h,stride; u32 *pixels; u32 aux,owns; } Bitmap;
extern void panel_previous(void *,Bitmap *,int,int,int,int,int,int,int,int,int);
extern void panel_fill(Bitmap *,int,int,int,int,int);
extern int panel_text(Bitmap *,void *,int,int,int,const char *,...);
extern int panel_effective(void *,int,int);
extern void fusion2_recovery_fmt(int,char *);
extern void fusion2_auto_fmt(int,char *);

/* Native pre-widget value labels also use these formatters. Keep them <=4
 * characters so the native draw cannot leave wide ms text outside our cell.
 * Storage/time equations remain unchanged; seconds are rounded to0.1s here.
 */
static void short_time(int raw,char *buf,int automatic) {
  char text[8]; int n=0,k=0,t;
  if(automatic)fusion2_auto_fmt(raw,text);else fusion2_recovery_fmt(raw,text);
  if(text[0]=='O'){buf[0]='O';buf[1]='F';buf[2]='F';buf[3]=0;return;}
  while(text[k]>='0' && text[k]<='9'){n=n*10+text[k]-'0';++k;}
  if(n<100){
    k=0;if(n>=10)buf[k++]='0'+n/10;
    buf[k++]='0'+n%10;buf[k++]='m';buf[k++]='s';buf[k]=0;
  }else{
    t=(n+50)/100;buf[0]='0'+t/10;buf[1]='.';buf[2]='0'+t%10;buf[3]='s';buf[4]=0;
  }
}
void panel_recovery_short(int raw,char *buf){short_time(raw,buf,0);}
void panel_auto_short(int raw,char *buf){short_time(raw,buf,1);}

static void fill(Bitmap *b,int x,int y,int xx,int yy) { panel_fill(b,x,y,xx,yy,1); }
static void line(Bitmap *b,int x,int y,int xx,int yy) {
  int dx=xx-x,dy=yy-y,i,n;
  n=dx<0?-dx:dx; i=dy<0?-dy:dy;if(i>n)n=i;
  for(i=0;i<=n;++i)fill(b,x+(n?dx*i/n:0),y+(n?dy*i/n:0),x+(n?dx*i/n:0),y+(n?dy*i/n:0));
}
static void box(Bitmap *b,int x,int y) {
  panel_fill(b,x-3,y+7,x+21,y+24,0);
  fill(b,x-2,y+8,x+20,y+8); fill(b,x-2,y+24,x+20,y+24);
  fill(b,x-2,y+8,x-2,y+24); fill(b,x+20,y+8,x+20,y+24);
}
static void number(Bitmap *b,int x,int y,int v) {
  panel_text(b,(void *)0x44d2e70c,x+9,y+16,2,"%d",v);
}
static void rail(Bitmap *b,int x,int y,int v,int center) {
  int n=(v*16+63)/127;
  fill(b,x+1,y+11,x+17,y+11);
  if(center) fill(b,x+9,y+9,x+9,y+13);
  fill(b,x+1+n,y+10,x+1+n,y+12);
}
void panel_draw(void *v,Bitmap *b,int x,int y,int code,int raw,
                int a,int c,int d,int e,int f) {
  int value,i,n,slot;
  panel_previous(v,b,x,y,code,raw,a,c,d,e,f);
  if(!b || b->w!=128 || b->h!=64 || b->stride!=2 || !b->pixels ||
     x<3 || x>106 || y< -2 || y>38) return;
  /* Existing native TUNE/code248, all INFO text, empty cells, labels and
   * p-lock outlines remain outside our glyph interior. */
  if(code==282 || code==285 || code==288 || code==291 || code==294 || code==297) {
    slot=raw; if(slot<0)slot=0; if(slot>2047)slot=2047;
    box(b,x,y); number(b,x,y,slot&1023);
    /* A/B is determined by actual sample descriptor, not assumed row position.
     * Arrow is stored direction, not a measured playback cursor. */
    panel_text(b,(void *)0x44d2e70c,x+2,y+10,2,"%s",
       code==282 || code==288 || code==294 ? "A":"B");
    fill(b,x+9,y+11,x+17,y+11);
    if(slot&1024) { fill(b,x+9,y+10,x+9,y+12); fill(b,x+10,y+9,x+10,y+13); }
    else { fill(b,x+17,y+10,x+17,y+12); fill(b,x+16,y+9,x+16,y+13); }
    return;
  }
  if(code==293 || code==287) {
    n=raw>>8; if(n<0)n=0; if(n>4)n=4;
    box(b,x,y);
    /* Named mode + compact relationship. This is a processing schematic,
     * not a claim to expose the internal detector/envelope/phase state. */
    panel_text(b,(void *)0x44d2e70c,x+9,y+17,2,"%s",
       n==0?"BLND":n==1?"RING":n==2?"DUCK":n==3?"MRPH":"GATE");
    panel_text(b,(void *)0x44d2e70c,x+1,y+10,2,"A");
    panel_text(b,(void *)0x44d2e70c,x+17,y+10,2,"B");
    if(n==0){fill(b,x+8,y+12,x+10,y+12);fill(b,x+9,y+11,x+9,y+13);}
    if(n==1){line(b,x+8,y+11,x+10,y+13);line(b,x+8,y+13,x+10,y+11);}
    if(n==2 || n==4){fill(b,x+7,y+12,x+12,y+12);fill(b,x+11,y+11,x+11,y+13);}
    if(n==3){fill(b,x+6,y+12,x+12,y+12);fill(b,x+6,y+11,x+6,y+13);fill(b,x+12,y+11,x+12,y+13);}
    return;
  }
  if(code==284){
    value=raw;if(value<0)value=0;if(value>32512)value=32512;
    value>>=8;box(b,x,y);number(b,x,y,value);rail(b,x,y,value,1);return;
  }
  if(code>=302 && code<=309) {
    value=raw; if(value<0)value=0;if(value>32512)value=32512;
    value=(value*127+16256)/32512; box(b,x,y);
    if(code==308 || code==309) {
      /* Keep Repair4's accurate bounded ms/OFF formatter visible. */
      char text[8]; int ms=0,k=0;
      if(code==308) fusion2_recovery_fmt(raw,text);else fusion2_auto_fmt(raw,text);
      while(text[k]>='0' && text[k]<='9'){ms=ms*10+text[k]-'0';++k;}
      if(text[0]=='O')panel_text(b,(void *)0x44d2e70c,x+9,y+16,2,"OFF");
      else if(ms<1000)panel_text(b,(void *)0x44d2e70c,x+9,y+16,2,"%dms",ms);
      else panel_text(b,(void *)0x44d2e70c,x+9,y+16,2,"%d.%02ds",ms/1000,(ms%1000)/10);
    } else number(b,x,y,(value*100+63)/127);
    rail(b,x,y,value,code==302 || code==305 || code==307);
    return;
  }
  /* FANG parameter codes are appended after the immutable Repair4 rows. */
  if(code<314 || code>320)return;
  value=raw; if(value<0)value=0;if(value>32512)value=32512;
  value=value>>8; box(b,x,y);number(b,x,y,value);
  if(code==314) {
    /* Primary oscillator shape schematic: actual continuous saw/pulse blend. */
    for(i=0;i<16;++i) {
      int saw=i*4/15-2,pulse=i<8?2:-2;
      int yy=11+(saw*(127-value)+pulse*value)/127;
      fill(b,x+1+i,y+yy,x+1+i,y+yy);
    }
  } else if(code==315 || code==316) {
    int other=panel_effective(v,code==315?316:315,-1),cut,res,rise;
    if(other<0)other=0;
    if(other>32512)other=32512;
    other>>=8;
    cut=code==315?value:other;res=code==316?value:other;
    n=(cut*12+63)/127;rise=(res*3+63)/127;
    fill(b,x+1,y+11,x+1+n,y+11);
    line(b,x+1+n,y+11,x+2+n,y+11+rise);
    line(b,x+2+n,y+11+rise,x+4+n,y+9);
  } else if(code==317 || code==318) {
    int height=code==317?1+value*3/127:4;
    n=code==318 ? 4+value*12/127 : 12;
    line(b,x+1,y+9,x+3,y+9+height);
    for(i=0;i<n;++i)fill(b,x+3+i,y+9+height-i*height/n,x+3+i,y+9+height-i*height/n);
  } else rail(b,x,y,value,0);
}
