
!function(){try{var e="undefined"!=typeof window?window:"undefined"!=typeof global?global:"undefined"!=typeof globalThis?globalThis:"undefined"!=typeof self?self:{},n=(new e.Error).stack;n&&(e._sentryDebugIds=e._sentryDebugIds||{},e._sentryDebugIds[n]="d12b385d-dba5-59f7-98b3-77fd32a3436b")}catch(e){}}();
import{a as p}from"./chunk-QOTEJF7J.js";import{i as f,m as l}from"./chunk-W4MC63X3.js";function m(o,e){let t=+f(o)-+f(e);return t<0?-1:t>0?1:t}function d(o,e,t){let[n,r]=l(t?.in,o,e),a=n.getFullYear()-r.getFullYear(),i=n.getMonth()-r.getMonth();return a*12+i}function h(o,e){let t=f(o,e?.in),n=t.getMonth();return t.setFullYear(t.getFullYear(),n+1,0),t.setHours(23,59,59,999),t}function D(o,e){let t=f(o,e?.in);return+p(t,e)==+h(t,e)}function k(o,e,t){let[n,r,a]=l(t?.in,o,o,e),i=m(r,a),s=Math.abs(d(r,a));if(s<1)return 0;r.getMonth()===1&&r.getDate()>27&&r.setDate(30),r.setMonth(r.getMonth()-i*s);let u=m(r,a)===-i;D(n)&&s===1&&m(n,a)===1&&(u=!1);let c=i*(s-+u);return c===0?0:c}export{m as a,k as b};
/**i18n:2679c7a8d3bece6d756b211e9e9005dcae23582a5802ad1a8ada3e355c32efef*/
//# 

//# debugId=d12b385d-dba5-59f7-98b3-77fd32a3436b
