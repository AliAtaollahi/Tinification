// Plot in the console UI
cpg.method.fullNameExact("HVAC$Controller.getSense:void(int)").plotDotAst

// Or get the DOT text (safe even if empty)
val astDot = cpg.method.fullNameExact("HVAC$Controller.getSense:void(int)").dotAst.l.headOption.getOrElse("")
astDot