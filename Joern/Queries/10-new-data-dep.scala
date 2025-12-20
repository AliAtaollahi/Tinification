import io.shiftleft.semanticcpg.language._
import io.shiftleft.codepropertygraph.generated.nodes.Method

import scala.collection.mutable
import scala.util.matching.Regex
import java.util.regex.Pattern

/**
 * Usage in Joern:
 * :load /path/to/this.scala
 * val dot = CfgByClass.run(".*AB_StaticEquivalent\\.java")
 */
object CfgByClass {

  // ---------------- helpers ----------------

  def ownerTypeOf(fullName: String): String =
    fullName.takeWhile(_ != '.')

  def simpleTypeName(tf: String): String =
    Option(tf).getOrElse("").split("[.$]").lastOption.getOrElse(tf)

  // Build "Owner.method(p1, p2, ...)" from CPG; prefer param names, fall back to types; skip implicit 'this'
  def prettyMethodLabel(m: Method): String = {
    val owner = m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName))
    val params =
      m.parameter.orderGt(0).l.map { p =>
        val nm  = Option(p.name).map(_.trim).filter(n => n.nonEmpty && n != "this")
        val tpe = simpleTypeName(Option(p.typeFullName).getOrElse(""))
        nm.getOrElse(tpe)
      }.mkString(", ")
    s"$owner.${m.name}($params)"
  }

  // Prefix every numeric node id with a stable method-based prefix
  def prefixIds(dot: String, prefix: String): String = {
    val idRx = "\"(\\d+)\"".r
    idRx.replaceAllIn(dot, m => "\"" + prefix + "_" + m.group(1) + "\"")
  }

  // Remove outer digraph wrapper (so we can drop inside clusters)
  def stripDotWrapper(dot: String): String =
    dot.linesIterator
      .filterNot(_.trim.startsWith("digraph"))
      .filterNot(_.trim.startsWith("node "))
      .filterNot(_.trim == "}")
      .mkString("\n")

  // HTML-escape for Graphviz HTML-like labels
  def html(s: String): String =
    s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

  // ---------------- def/use extraction ----------------

  private val thisFieldRx: Regex = "\\bthis\\.([A-Za-z_]\\w*)".r

  // assignment operators: include compound assigns; plain "=" must not be "==" etc.
  private val assignOpRx: Regex =
    "(\\+=|-=|\\*=|/=|%=|<<=|>>=|\\|=|&=|\\^=|(?<![!<>=])=(?!=))".r

  private def wordRx(name: String): Regex =
    ("(?<![\\w$])" + Pattern.quote(name) + "(?![\\w$])").r

  private def maybeThisFieldRx(field: String): Regex =
    ("(?<![\\w$])(?:this\\.)?" + Pattern.quote(field) + "(?![\\w$])").r

  /**
    * Extract def/use variable keys from a single line-level code string.
    * Track only:
    *   SV::<field>  for this.field
    *   P::<param>   for parameters
    */
  private def extractDefsUses(
      code0: String,
      ownerFields: Set[String],
      paramNames: Set[String]
  ): (Set[String], Set[String]) = {

    val code = Option(code0).getOrElse("").trim
    val defs = mutable.Set.empty[String]
    val uses = mutable.Set.empty[String]

    def addFieldDef(f: String): Unit = defs += s"SV::$f"
    def addFieldUse(f: String): Unit = uses += s"SV::$f"
    def addParamDef(p: String): Unit = defs += s"P::$p"
    def addParamUse(p: String): Unit = uses += s"P::$p"

    // ++ / -- are read-modify-write
    ownerFields.foreach { f =>
      val base = maybeThisFieldRx(f).regex
      val rx1  = (base + "\\s*(\\+\\+|--)").r
      val rx2  = ("(\\+\\+|--)\\s*" + base).r
      if (rx1.findFirstIn(code).nonEmpty || rx2.findFirstIn(code).nonEmpty) {
        addFieldDef(f); addFieldUse(f)
      }
    }
    paramNames.foreach { p =>
      val base = wordRx(p).regex
      val rx1  = (base + "\\s*(\\+\\+|--)").r
      val rx2  = ("(\\+\\+|--)\\s*" + base).r
      if (rx1.findFirstIn(code).nonEmpty || rx2.findFirstIn(code).nonEmpty) {
        addParamDef(p); addParamUse(p)
      }
    }

    // Split on first assignment operator to separate LHS/RHS
    assignOpRx.findFirstMatchIn(code) match {
      case Some(m) =>
        val op  = m.group(1)
        val lhs = code.substring(0, m.start)
        val rhs = code.substring(m.end)

        // defs: vars in LHS
        ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(lhs).nonEmpty) addFieldDef(f) }
        paramNames.foreach { p => if (wordRx(p).findFirstIn(lhs).nonEmpty) addParamDef(p) }

        // uses: vars in RHS
        ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(rhs).nonEmpty) addFieldUse(f) }
        paramNames.foreach { p => if (wordRx(p).findFirstIn(rhs).nonEmpty) addParamUse(p) }

        // compound assigns read old LHS value too
        if (op != "=") {
          ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(lhs).nonEmpty) addFieldUse(f) }
          paramNames.foreach { p => if (wordRx(p).findFirstIn(lhs).nonEmpty) addParamUse(p) }
        }

      case None =>
        // no assignment: everything is use
        ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(code).nonEmpty) addFieldUse(f) }
        paramNames.foreach { p => if (wordRx(p).findFirstIn(code).nonEmpty) addParamUse(p) }
    }

    (defs.toSet, uses.toSet)
  }

  // ---------------- abstraction with root relabel ----------------

  case class AbsNode(id: String, kind: String, line: String, code: String, methodName: Option[String] = None)

  case class AbstractedCfg(
    dot: String,                         // full digraph text
    nodes: List[AbsNode],                // kept nodes (one per line)
    cfgEdges: Set[(String,String)],      // kept CFG edges after line-collapse
    rootIds: Set[String]                 // root node ids (unprefixed)
  )

  /**
   * Collapses nodes per source line to the longest code, rewires CFG edges accordingly.
   * Outputs CFG edges labeled "cfg" (NOT control dependence).
   */
  def abstractDotByLine(dot: String, rootLabelOverride: Option[String] = None): AbstractedCfg = {
    val nodeRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r

    val simpleLabelRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,><]+)>\\s*\\]".r

    val edgeRx: Regex =
      "\"(\\d+)\"\\s*->\\s*\"(\\d+)\"".r

    val nodesAll: List[AbsNode] =
      nodeRx.findAllMatchIn(dot).map { m =>
        val kind = m.group(2)
        val code = m.group(4)
        val methName =
          if (kind.startsWith("METHOD") || kind.equalsIgnoreCase("init")) Some(code) else None
        AbsNode(m.group(1), kind, m.group(3), code, methName)
      }.toList

    val edgesAll: List[(String, String)] =
      edgeRx.findAllMatchIn(dot).map(m => (m.group(1), m.group(2))).toList

    val labelRootIds: Set[String] =
      simpleLabelRx.findAllMatchIn(dot).map(_.group(1)).toSet

    val srcs = edgesAll.map(_._1).toSet
    val dsts = edgesAll.map(_._2).toSet
    val structuralRootIds: Set[String] = srcs -- dsts

    val methodRootIds =
      nodesAll.filter(n => n.kind.startsWith("METHOD") || n.kind.equalsIgnoreCase("init")).map(_.id).toSet

    val rootIds: Set[String] =
      if (methodRootIds.nonEmpty) methodRootIds else (labelRootIds ++ structuralRootIds)

    if (nodesAll.isEmpty && edgesAll.isEmpty)
      return AbstractedCfg("""digraph "cfg_abstract" { node [shape="rect"]; }""", Nil, Set.empty, Set.empty)

    // keep one node per line (longest code)
    val bestByLine: Map[String, AbsNode] =
      nodesAll.groupBy(_.line).map { case (line, xs) => line -> xs.maxBy(_.code.length) }

    val nodeById: Map[String, AbsNode] = nodesAll.iterator.map(n => n.id -> n).toMap
    val keptNodes: List[AbsNode] = bestByLine.values.toList

    // rewire edges using the kept representative of each line
    val keptCfgEdges: Set[(String,String)] =
      edgesAll.flatMap { case (srcId, dstId) =>
        val adjSrc = nodeById.get(srcId).flatMap(n => bestByLine.get(n.line)).map(_.id).getOrElse(srcId)
        val adjDst = nodeById.get(dstId).flatMap(n => bestByLine.get(n.line)).map(_.id).getOrElse(dstId)
        if (adjSrc != adjDst) Some(adjSrc -> adjDst) else None
      }.toSet

    val nodeLines: List[String] =
      keptNodes
        .sortBy(n => n.line.toIntOption.getOrElse(Int.MaxValue))
        .map { n =>
          val label =
            n.kind match {
              case k if k.startsWith("METHOD") && n.methodName.isDefined => n.methodName.get
              case k if k.equalsIgnoreCase("init") && n.methodName.isDefined => n.methodName.get
              case _ => s"${n.kind}, ${n.line}<BR/>${n.code}"
            }
          s""""${n.id}" [label = <${label}> ]"""
        }

    val edgeLines: List[String] =
      keptCfgEdges.toList.map { case (s, t) =>
        s""""$s" -> "$t" [label="cfg"]"""
      }

    val rootLines: List[String] =
      if (rootIds.nonEmpty) {
        val text = html(rootLabelOverride.getOrElse("METHOD"))
        rootIds.toList.map { id => s""""$id" [label = <<FONT>${text}</FONT>> ]""" }
      } else Nil

    val outDot =
      s"""digraph "cfg_abstract" {
node [shape="rect"];
${(nodeLines ++ edgeLines ++ rootLines).mkString("\n")}
}"""

    AbstractedCfg(outDot, keptNodes, keptCfgEdges, rootIds)
  }

  // ---------------- main entry ----------------

  def run(fileRegex: String = ".*\\.java"): String = {
    val methods =
      cpg.method
        .where(_.isExternal(false))
        .where(_.file.name(fileRegex))
        .l

    val methodsByOwner =
      methods.groupBy { m => m.typeDecl.name.headOption.getOrElse(ownerTypeOf(m.fullName)) }

    val allDataDepEdges = mutable.Set.empty[String]

    val clustersByClass: List[String] =
      methodsByOwner.toList.sortBy(_._1).flatMap { case (owner, ms) =>
        val ownerSan = owner.replaceAll("[^A-Za-z0-9_]", "_")

        val bodies: List[String] = ms.flatMap { m =>
          val raw = m.dotCfg.l.headOption.getOrElse("")
          if (raw.isEmpty) None
          else {
            val rootLabel = prettyMethodLabel(m)
            val absCfg    = abstractDotByLine(raw, Some(rootLabel))

            val pfx  = (owner + "__" + m.fullName).replaceAll("[^A-Za-z0-9_]", "_")
            val body = stripDotWrapper(prefixIds(absCfg.dot, pfx))

            // ---------------- Data dependency (Definition 5 via reaching definitions) ----------------

            def pref(id: String): String = pfx + "_" + id

            // prefixed nodes + line numbers
            val prefNodes: List[AbsNode] =
              absCfg.nodes.map(n => n.copy(id = pref(n.id)))

            val stmtIds: Set[String] = prefNodes.map(_.id).toSet

            val lineByNode: Map[String, Int] =
              prefNodes.map(n => n.id -> n.line.toIntOption.getOrElse(Int.MaxValue)).toMap

            val codeByNode: Map[String, String] =
              prefNodes.map(n => n.id -> n.code).toMap

            val entryIdOpt: Option[String] =
              absCfg.rootIds.toList.sorted.headOption.map(pref)

            // CFG preds/succs from collapsed CFG edges
            val preds = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)
            val succs = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)

            absCfg.cfgEdges.foreach { case (s0, t0) =>
              val s = pref(s0); val t = pref(t0)
              if (stmtIds.contains(s) && stmtIds.contains(t)) {
                succs(s) = succs(s) + t
                preds(t) = preds(t) + s
              }
            }

            // track fields that actually appear as this.<field>
            val declaredFields: Set[String] = m.typeDecl.member.name.l.toSet
            val usedFields: Set[String] =
              absCfg.nodes.flatMap(n => thisFieldRx.findAllMatchIn(n.code).map(_.group(1))).toSet
            val fieldsTracked: Set[String] = usedFields.intersect(declaredFields)

            // parameters by name (skip implicit this already)
            val paramsTracked: Set[String] =
              m.parameter.orderGt(0).name.l.map(_.trim).filter(_.nonEmpty).toSet

            // def/use sets per node
            val defVars = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)
            val useVars = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)

            stmtIds.foreach { id =>
              val code = codeByNode.getOrElse(id, "")
              val (defs, uses) = extractDefsUses(code, fieldsTracked, paramsTracked)
              defVars(id) = defs
              useVars(id) = uses
            }

            // reaching definitions domain: (varKey, defNodeId)
            type DefPair = (String, String)

            // seed parameters as defined at entry
            val initialDefs: Set[DefPair] =
              entryIdOpt.toSet.flatMap { entry =>
                paramsTracked.map(p => (s"P::$p", entry))
              }

            def gen(id: String): Set[DefPair] = defVars(id).map(v => (v, id))

            // IN / OUT
            var inMap: Map[String, Set[DefPair]] =
              stmtIds.map(id => id -> Set.empty[DefPair]).toMap ++
                entryIdOpt.map(eid => eid -> initialDefs)

            var outMap: Map[String, Set[DefPair]] =
              stmtIds.map { id =>
                val in0  = if (entryIdOpt.contains(id)) initialDefs else Set.empty[DefPair]
                val out0 = gen(id) ++ in0.filterNot { case (v, _) => defVars(id).contains(v) }
                id -> out0
              }.toMap

            // fixpoint
            var changed = true
            while (changed) {
              changed = false
              for (id <- stmtIds) {
                val newIn: Set[DefPair] =
                  if (entryIdOpt.contains(id)) initialDefs
                  else preds(id).flatMap(p => outMap.getOrElse(p, Set.empty)).toSet

                val newOut: Set[DefPair] =
                  gen(id) ++ newIn.filterNot { case (v, _) => defVars(id).contains(v) }

                if (newIn != inMap.getOrElse(id, Set.empty) || newOut != outMap.getOrElse(id, Set.empty)) {
                  inMap  = inMap.updated(id, newIn)
                  outMap = outMap.updated(id, newOut)
                  changed = true
                }
              }
            }

            // Emit data dep edges but DO NOT allow "backwards" deps by line (your requirement)
            for (useNodeId <- stmtIds; v <- useVars(useNodeId)) {
              val reachingDefs: Set[String] =
                inMap.getOrElse(useNodeId, Set.empty).collect { case (`v`, defId) => defId }

              reachingDefs.foreach { defId =>
                if (defId != useNodeId) {
                  val ok =
                    entryIdOpt.contains(defId) || {
                      val dl = lineByNode.getOrElse(defId, Int.MaxValue)
                      val ul = lineByNode.getOrElse(useNodeId, Int.MaxValue)
                      dl < ul
                    }

                  if (ok) {
                    allDataDepEdges += s""""$defId" -> "$useNodeId" [label="data dep", color="purple"]"""
                  }
                }
              }
            }

            Some(body)
          }
        }

        if (bodies.isEmpty) None
        else Some(
          s"""subgraph "cluster_$ownerSan" {
  label="${html(owner)}";
  ${bodies.mkString("\n")}
}"""
        )
      }

    val out =
      s"""digraph "program_cfg_abstract_by_class" {
compound=true;
node [shape="rect"];
${clustersByClass.mkString("\n\n")}

${allDataDepEdges.toList.sorted.mkString("\n")}
}"""

    println(out)
    out
  }
}

// Example run (use your real file name)
val dot = CfgByClass.run(".*AB_StaticEquivalent\\.java")
