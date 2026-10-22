
!function(){try{var e="undefined"!=typeof window?window:"undefined"!=typeof global?global:"undefined"!=typeof globalThis?globalThis:"undefined"!=typeof self?self:{},n=(new e.Error).stack;n&&(e._sentryDebugIds=e._sentryDebugIds||{},e._sentryDebugIds[n]="dd361a89-9f12-5efb-84e6-75f8b70549d9")}catch(e){}}();
import{p as c,s as n}from"./chunk-2WOY3CWP.js";import{Ca as o,xa as i}from"./chunk-5NWV34XP.js";var p=(()=>{class s{constructor(e){this._snackbar=e}handle(e,r={form:null,snack:!0,cdr:null}){if(r.form){r.form.setErrors({serverError:e.message});for(let[a,t]of Object.entries(e.details||{}))typeof t=="string"?r.form.get(a).setErrors({serverError:t}):Array.isArray(t)&&r.form.get(a).setErrors({serverError:t.join(`
`)})}if(r.snack){let a=Object.keys(e.details??[]).reduce((t,f)=>`${e.details[t]??""}${e.details[f]}
`,"")||e.message;this._snackbar.add({text:a,type:c.ERROR})}r.cdr&&r.cdr.detectChanges()}static{this.\u0275fac=function(r){return new(r||s)(o(n))}}static{this.\u0275prov=i({token:s,factory:s.\u0275fac,providedIn:"root"})}}return s})();export{p as a};
/**i18n:2679c7a8d3bece6d756b211e9e9005dcae23582a5802ad1a8ada3e355c32efef*/
//# 

//# debugId=dd361a89-9f12-5efb-84e6-75f8b70549d9
