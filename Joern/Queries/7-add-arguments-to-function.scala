// Get all methods (function definitions) along with their arguments
val methods = cpg.method.l

// Iterate through methods and print their names along with their arguments
methods.foreach { method =>
  val methodName = method.fullName
  val arguments = method.parameter.l.map(_.name).mkString(", ")
  println(s"Method: $methodName, Arguments: $arguments")
}
