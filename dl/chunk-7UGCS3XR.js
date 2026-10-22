
!function(){try{var e="undefined"!=typeof window?window:"undefined"!=typeof global?global:"undefined"!=typeof globalThis?globalThis:"undefined"!=typeof self?self:{},n=(new e.Error).stack;n&&(e._sentryDebugIds=e._sentryDebugIds||{},e._sentryDebugIds[n]="664bc8a0-2799-538b-9052-95cf96886b3e")}catch(e){}}();
import{a}from"./chunk-WPGWTQST.js";import{Da as e,m as o,xa as s}from"./chunk-5NWV34XP.js";var g=(()=>{class t{set isDragging(i){i?this._doc.body.classList.add("is-dragging"):this._doc.body.classList.remove("is-dragging")}constructor(){this._doc=e(a),this._loading=new o(!1),this.loading$=this._loading.asObservable(),this._counter=0,this.loading$=this._loading.asObservable()}loadingOn(){this._counter+=1,this._loading.next(!0)}loadingOff(){this._counter=Math.max(this._counter-1,0),this._counter===0&&this._loading.next(!1)}static{this.\u0275fac=function(n){return new(n||t)}}static{this.\u0275prov=s({token:t,factory:t.\u0275fac,providedIn:"root"})}}return t})();export{g as a};
/**i18n:2679c7a8d3bece6d756b211e9e9005dcae23582a5802ad1a8ada3e355c32efef*/
//# 

//# debugId=664bc8a0-2799-538b-9052-95cf96886b3e
