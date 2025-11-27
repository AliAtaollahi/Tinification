// Collapse a DOT CFG (one node per source line; prefer calls/assignments)
def abstractDotByLine(dot: String): String = {
  import scala.collection.mutable
  val nodeRx = "\"(\\d+)\" \\[label = <([^,]+), (\\d+)<BR/>(.*?)> \\]".r
  val edgeRx = "\"(\\d+)\" -> \"(\\d+)\"".r

  // id -> (label, line, code)
  val nodes = mutable.Map.empty[String,(String,Int,String)]
  for (m <- nodeRx.findAllMatchIn(dot)) {
    val id    = m.group(1)
    val label = m.group(2)
    val line  = m.group(3).toInt
    val code  = m.group(4)
    // drop method header/return boxes
    if (label != "METHOD" && label != "METHOD_RETURN") {
      nodes(id) = (label, line, code)
    }
  }

  // Prefer real statements over operator/field noise
  def score(label: String): Int = {
    if (label == "FIELD_IDENTIFIER") 1
    else if (label.startsWith("<operator>")) {
      if (label.contains("assignment")) 6
      else if (label.contains("logical") || label.contains("equals") ||
               label.contains("greater") || label.contains("less")) 3
      else if (label.contains("fieldAccess")) 1
      else 2
    } else 10 // likely a normal call like start/finish/activateh/switchoff
  }

  // Pick one representative node per line
  val repsByLine: Map[Int,(String,String,Int,String)] =
    nodes.toList
      .groupBy{ case (_,(_,line,_)) => line }
      .map { case (ln, lst) =>
        val (bestId,(bestLabel,_,bestCode)) =
          lst.maxBy{ case (_, (lbl,_,code)) => (score(lbl), code.length) }
        ln -> (bestId, bestLabel, ln, bestCode)
      }

  // id -> representative id (by the line it sits on)
  val repIdForLine: Map[Int,String] = repsByLine.map { case (ln,(id,_,_,_)) => ln -> id }
  val idToRep: Map[String,String] =
    nodes.map { case (id,(_,ln,_)) => id -> repIdForLine.getOrElse(ln, id) }.toMap

  // Collect deduped edges between representatives only
  val edges = mutable.LinkedHashSet.empty[(String,String)]
  for (m <- edgeRx.findAllMatchIn(dot)) {
    val a0 = m.group(1); val b0 = m.group(2)
    (idToRep.get(a0), idToRep.get(b0)) match {
      case (Some(a), Some(b)) if a != b => edges += ((a,b))
      case _ => // ignore
    }
  }

  // Emit abstracted DOT
  val sb = new StringBuilder
  sb.append("digraph \"cfg_abstract\" {\nnode [shape=\"rect\"];\n")
  repsByLine.toSeq.sortBy(_._1).foreach { case (_, (id, label, line, code)) =>
    sb.append(s"\"$id\" [label = <${label}, ${line}<BR/>${code}> ]\n")
  }
  edges.foreach { case (a,b) => sb.append(s"\"$a\" -> \"$b\"\n") }
  sb.append("}\n").toString
}

// === Use it on getSense (example) ===
val cfgDot       = cpg.method.fullNameExact("HVAC$Controller.getSense:void(int)").dotCfg.l.headOption.getOrElse("")
val cfg_abstract = abstractDotByLine(cfgDot)

// (optional) write to file so you can render with dot:
// import java.nio.file.{Files,Paths}; Files.write(Paths.get("getSense_cfg_abstract.dot"), cfg_abstract.getBytes("UTF-8"))
// then: dot -Tpng getSense_cfg_abstract.dot > getSense_cfg_abstract.png
