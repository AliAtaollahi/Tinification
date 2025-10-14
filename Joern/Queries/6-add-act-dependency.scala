// Get all dynamic dispatch calls into a list
val dynamicDispatchCallsList = dynamicDispatchCalls.filter(_.dispatchType == "DYNAMIC_DISPATCH")

// Print the details of the dynamic dispatch calls
dynamicDispatchCallsList.foreach { call =>
  println(s"Dispatch Type: ${call.dispatchType}, Method Full Name: ${call.methodFullName}, Line: ${call.lineNumber.getOrElse("N/A")}")
}
