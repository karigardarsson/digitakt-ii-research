/* SY FANG: original polyBLEP oscillator + nonlinear four-stage lowpass.
 * No Plaits engine transplant. Private per-track state; stock AMP/filter/FX
 * remain downstream. Plain C99 shared by host and Selache target tests.
 */
typedef struct {
  float pitch,tune,wave,cut,res,env,dec,glide,bite,velocity;
  int active,trigger,release,reset;
} FangFrame;
typedef struct {
  float phase,sub,freq,env,z0,z1,z2,z3,dcx,dcy;
  int armed;
} Fang;
#ifdef FANG_HOST
Fang fang_states[16];
#else
extern Fang fang_states[16];
#endif
typedef char fang_state_size[(sizeof(Fang)==44)?1:-1];
typedef char fang_frame_size[(sizeof(FangFrame)==56)?1:-1];
static float limit(float v,float lo,float hi){return v<lo?lo:(v>hi?hi:v);}
static float sat(float x){return x/(1.0f+(x<0.0f?-x:x));}
static float blep(float t,float dt){
  float q;
  if(t<dt){q=t/dt;return q+q-q*q-1.0f;}
  if(t>1.0f-dt){q=(t-1.0f)/dt;return q*q+q+q+1.0f;}
  return 0.0f;
}
static float exp2small(float x){
  float p=1.0f;
  while(x<0.0f){x+=1.0f;p*=0.5f;}
  while(x>1.0f){x-=1.0f;p*=2.0f;}
  /* Fifth-order Taylor on [0,1], <0.009% error at the endpoint. */
  p*=1.0f+x*(0.69314718f+x*(0.24022651f+x*(0.05550411f+x*(0.00961813f+x*0.00133336f))));
  return p;
}
void fang_track(int track,FangFrame *f,float *out){
  Fang *s;int i;float target,w,cut,res,env,dec,glide,bite,velocity,g,feedback,drive;
  if(track<0 || track>15)return;
  s=fang_states+track;
  if(f->reset || !f->active){
    s->phase=0.0f;s->sub=0.0f;s->freq=0.0f;s->env=0.0f;
    s->z0=0.0f;s->z1=0.0f;s->z2=0.0f;s->z3=0.0f;s->dcx=0.0f;s->dcy=0.0f;s->armed=0;
    for(i=0;i<32;++i)out[i]=0.0f;
    if(!f->trigger || !f->active)return;
  }
  target=440.0f/48000.0f*exp2small((limit((f->pitch+f->tune-16384.0f)/256.0f,0.0f,127.0f)-69.0f)/12.0f);
  target=limit(target,0.00015f,0.18f);
  w=limit(f->wave/32512.0f,0.0f,1.0f);cut=limit(f->cut/32512.0f,0.0f,1.0f);
  res=limit(f->res/32512.0f,0.0f,1.0f);env=limit(f->env/32512.0f,0.0f,1.0f);
  dec=limit(f->dec/32512.0f,0.0f,1.0f);glide=limit(f->glide/32512.0f,0.0f,1.0f);
  bite=limit(f->bite/32512.0f,0.0f,1.0f);velocity=limit(f->velocity/32512.0f,0.0f,1.0f);
  if(!s->armed){
    if(!f->trigger){for(i=0;i<32;++i)out[i]=0.0f;return;}
    s->freq=target;s->armed=1;s->env=1.0f;
  }
  if(f->trigger)s->env=1.0f;
  feedback=3.7f*res;drive=1.0f+5.0f*bite;
  dec=1.0f/(1.0f+1.0f/(48000.0f*(0.015f+1.485f*dec*dec)));
  glide=glide<=0.0f?1.0f:1.0f/(1.0f+48000.0f*0.4f*glide*glide);
  for(i=0;i<32;++i){
    float dt,t,saw,pulse,x,u,y,dc;
    s->freq+=(target-s->freq)*glide;dt=s->freq;
    t=s->phase;saw=2.0f*t-1.0f-blep(t,dt);
    pulse=(t<0.5f?1.0f:-1.0f)+blep(t,dt)-blep(t<0.5f?t+0.5f:t-0.5f,dt);
    s->phase+=dt;if(s->phase>=1.0f)s->phase-=1.0f;
    s->sub+=0.5f*dt;if(s->sub>=1.0f)s->sub-=1.0f;
    x=saw+(pulse-saw)*w;
    /* Triangle sub is continuous; no naive square discontinuity. */
    x+=limit((w-0.5f)*2.0f,0.0f,1.0f)*0.35f*(1.0f-4.0f*(s->sub<0.5f?0.5f-s->sub:s->sub-0.5f));
    g=limit(0.0015f+0.32f*cut*cut+0.32f*env*s->env*(0.65f+0.7f*velocity*bite),0.0015f,0.65f);
    u=sat((x-feedback*s->z3)*drive);
    s->z0+=g*(u-sat(s->z0));
    s->z1+=g*(sat(s->z0)-sat(s->z1));
    s->z2+=g*(sat(s->z1)-sat(s->z2));
    s->z3+=g*(sat(s->z2)-sat(s->z3));
    y=0.35f*s->z3*(1.0f+res);
    dc=y-s->dcx+0.99935f*s->dcy;s->dcx=y;s->dcy=dc;
    out[i]=dc;s->env*=dec;
  }
}
