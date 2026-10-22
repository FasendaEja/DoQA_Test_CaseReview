
!function(){try{var e="undefined"!=typeof window?window:"undefined"!=typeof global?global:"undefined"!=typeof globalThis?globalThis:"undefined"!=typeof self?self:{},n=(new e.Error).stack;n&&(e._sentryDebugIds=e._sentryDebugIds||{},e._sentryDebugIds[n]="f10d4ee9-31f9-52ff-b48c-624424bd7ad3")}catch(e){}}();
var i=e=>{if(e?.value?.length){let a=/^[A-Za-z0-9!#$%'()*+,-:;=?@[\]^_`|~]+$/i;if(/[<>&{}[".\\]+/.test(e.value))return{passwordInvalid:"forbiddenSymbols"};if(!a.test(e.value))return{passwordInvalid:"onlyLatinCharacters"};if(!/[A-Z]+/.test(e.value))return{passwordInvalid:"noUppercase"};if(!/[a-z]+/.test(e.value))return{passwordInvalid:"noLowercase"};if(!/[0-9]+/.test(e.value))return{passwordInvalid:"noDigits"};if(e.value.length<8)return{passwordInvalid:"smallLength"}}return null};export{i as a};
/**i18n:2679c7a8d3bece6d756b211e9e9005dcae23582a5802ad1a8ada3e355c32efef*/
//# 

//# debugId=f10d4ee9-31f9-52ff-b48c-624424bd7ad3
