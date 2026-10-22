
!function(){try{var e="undefined"!=typeof window?window:"undefined"!=typeof global?global:"undefined"!=typeof globalThis?globalThis:"undefined"!=typeof self?self:{},n=(new e.Error).stack;n&&(e._sentryDebugIds=e._sentryDebugIds||{},e._sentryDebugIds[n]="d17a5745-5c07-5676-aba3-1d9421652eed")}catch(e){}}();
import{i as t}from"./chunk-MPNEIO73.js";import{i as u}from"./chunk-W4MC63X3.js";function b(l,s){let e=u(l,s?.in);if(isNaN(+e))throw new RangeError("Invalid time value");let i=s?.format??"extended",c=s?.representation??"complete",n="",r="",f=i==="extended"?"-":"",$=i==="extended"?":":"";if(c!=="time"){let o=t(e.getDate(),2),a=t(e.getMonth()+1,2);n=`${t(e.getFullYear(),4)}${f}${a}${f}${o}`}if(c!=="date"){let o=e.getTimezoneOffset();if(o!==0){let d=Math.abs(o),O=t(Math.trunc(d/60),2),x=t(d%60,2);r=`${o<0?"+":"-"}${O}:${x}`}else r="Z";let a=t(e.getHours(),2),m=t(e.getMinutes(),2),g=t(e.getSeconds(),2),h=n===""?"":"T",p=[a,m,g].join($);n=`${n}${h}${p}${r}`}return n}export{b as a};
/**i18n:2679c7a8d3bece6d756b211e9e9005dcae23582a5802ad1a8ada3e355c32efef*/
//# 

//# debugId=d17a5745-5c07-5676-aba3-1d9421652eed
